"""A bounded, thread-safe pool of SMTP connections.

The pool lazily dials and authenticates connections, reuses them after a
``NOOP`` liveness check, and discards a connection whose command fails, because
a failed command can leave unread replies on the wire.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

from mxraven._internal.smtp.session import Session
from mxraven.errors import MxRavenError, SMTPConnectionError, SMTPTimeoutError

if TYPE_CHECKING:
    from collections.abc import Iterable

    from mxraven._internal.smtp.driver import ConnectionConfig
    from mxraven._internal.smtp.transaction import Transaction
    from mxraven.result import Result

__all__ = ["Pool"]

_DEFAULT_SIZE = 5


class Pool:
    """A bounded pool of authenticated connections.

    The pool is safe for concurrent use. It bounds the number of live
    connections, including checked-out ones, and releases capacity when a dial
    fails.
    """

    def __init__(self, config: ConnectionConfig, size: int = _DEFAULT_SIZE) -> None:
        """Create the pool.

        Args:
            config: The connection parameters for newly dialed sessions.
            size: The maximum number of live connections. A non-positive value
                selects the default of five.
        """
        self._config = config
        self._size = size if size > 0 else _DEFAULT_SIZE
        self._idle: list[Session] = []
        self._owned: set[Session] = set()
        self._live = 0
        self._closed = False
        self._condition = threading.Condition()

    @property
    def size(self) -> int:
        """The maximum number of live connections."""
        return self._size

    @property
    def is_closed(self) -> bool:
        """Whether the pool has been closed."""
        return self._closed

    def acquire(self, timeout: float | None = None) -> Session:
        """Check out a healthy connection.

        Args:
            timeout: The maximum time to wait for a connection, in seconds, or
                `None` to wait indefinitely.

        Returns:
            A connected, authenticated session.

        Raises:
            SMTPConnectionError: The pool is closed, or dialing failed.
            SMTPTimeoutError: The wait for capacity exceeded `timeout`.
        """
        while True:
            session = self._acquire_existing(timeout)
            if session is None:
                message = "smtp: client is closed"
                raise SMTPConnectionError(message)
            if self._is_healthy(session):
                return session
            self.discard(session)

    def release(self, session: Session) -> None:
        """Return a session to the pool.

        A session the pool does not own is closed and ignored, so a foreign
        session cannot corrupt the pool.
        """
        foreign = False
        with self._condition:
            if session not in self._owned:
                foreign = True
            elif self._closed:
                self._owned.discard(session)
                self._live -= 1
                foreign = True
            else:
                self._idle.append(session)
                self._condition.notify()
        if foreign:
            session.discard()

    def discard(self, session: Session) -> None:
        """Close a session and release its capacity."""
        with self._condition:
            if session in self._owned:
                self._owned.discard(session)
                self._live -= 1
            self._condition.notify()
        session.discard()

    def close(self) -> None:
        """Close every idle connection and reject further use. Idempotent."""
        with self._condition:
            if self._closed:
                return
            self._closed = True
            idle = self._idle
            self._idle = []
            for session in idle:
                self._owned.discard(session)
            self._live -= len(idle)
            self._condition.notify_all()
        for session in idle:
            session.close()

    def send(
        self,
        transaction: Transaction,
        content: bytes | Iterable[bytes],
        *,
        prefer_bdat: bool = False,
    ) -> Result:
        """Submit one message over a pooled connection.

        A connection whose transaction fails is discarded, never reused.

        Raises:
            SMTPError: The server rejected a command.
            SMTPTransactionError: Every recipient was rejected.
        """
        session = self.acquire()
        try:
            result = session.send(transaction, content, prefer_bdat=prefer_bdat)
        except BaseException:
            self.discard(session)
            raise
        self.release(session)
        return result

    def _acquire_existing(self, timeout: float | None) -> Session | None:
        with self._condition:
            deadline = None if timeout is None else time.monotonic() + timeout
            while True:
                if self._closed:
                    return None
                if self._idle:
                    return self._idle.pop()
                if self._live < self._size:
                    self._live += 1
                    break
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    message = "smtp: timed out acquiring a connection"
                    raise SMTPTimeoutError(message)
                self._condition.wait(remaining)
        return self._dial()

    def _dial(self) -> Session:
        session = Session(self._config)
        try:
            session.connect()
        except BaseException:
            self._release_capacity()
            raise
        with self._condition:
            if self._closed:
                self._release_capacity_locked()
                session.discard()
                message = "smtp: client is closed"
                raise SMTPConnectionError(message)
            self._owned.add(session)
        return session

    def _release_capacity(self) -> None:
        with self._condition:
            self._release_capacity_locked()

    def _release_capacity_locked(self) -> None:
        self._live -= 1
        self._condition.notify()

    @staticmethod
    def _is_healthy(session: Session) -> bool:
        try:
            session.noop()
        except MxRavenError:
            return False
        return True
