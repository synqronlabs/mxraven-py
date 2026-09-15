"""A bounded asyncio pool of SMTP connections."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from mxraven._internal.aio.session import AsyncSession
from mxraven.errors import MxRavenError, SMTPConnectionError, SMTPTimeoutError

if TYPE_CHECKING:
    from collections.abc import Iterable

    from mxraven._internal.smtp.driver import ConnectionConfig
    from mxraven._internal.smtp.transaction import Transaction
    from mxraven.result import Result

__all__ = ["AsyncPool"]

_DEFAULT_SIZE = 5


class AsyncPool:
    """A bounded pool of authenticated connections for asyncio.

    The pool is safe for concurrent use within one event loop. It bounds the
    number of live connections and releases capacity when a dial fails.
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
        self._idle: list[AsyncSession] = []
        self._owned: set[AsyncSession] = set()
        self._live = 0
        self._closed = False
        self._condition = asyncio.Condition()

    @property
    def size(self) -> int:
        """The maximum number of live connections."""
        return self._size

    @property
    def is_closed(self) -> bool:
        """Whether the pool has been closed."""
        return self._closed

    async def acquire(self, timeout: float | None = None) -> AsyncSession:
        """Check out a healthy connection.

        Raises:
            SMTPConnectionError: The pool is closed, or dialing failed.
            SMTPTimeoutError: The wait for capacity exceeded `timeout`.
        """
        while True:
            session = await self._acquire_existing(timeout)
            if session is None:
                message = "smtp: client is closed"
                raise SMTPConnectionError(message)
            if await self._is_healthy(session):
                return session
            await self.discard(session)

    async def release(self, session: AsyncSession) -> None:
        """Return a session to the pool.

        A session the pool does not own is closed and ignored.
        """
        foreign = False
        async with self._condition:
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
            await session.discard()

    async def discard(self, session: AsyncSession) -> None:
        """Close a session and release its capacity."""
        async with self._condition:
            if session in self._owned:
                self._owned.discard(session)
                self._live -= 1
            self._condition.notify()
        await session.discard()

    async def close(self) -> None:
        """Close every idle connection and reject further use. Idempotent."""
        async with self._condition:
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
            await session.close()

    async def send(
        self,
        transaction: Transaction,
        content: bytes | Iterable[bytes],
        *,
        prefer_bdat: bool = False,
    ) -> Result:
        """Submit one message over a pooled connection."""
        session = await self.acquire()
        try:
            result = await session.send(transaction, content, prefer_bdat=prefer_bdat)
        except BaseException:
            await self.discard(session)
            raise
        await self.release(session)
        return result

    async def _acquire_existing(self, timeout: float | None) -> AsyncSession | None:
        async with self._condition:
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
                try:
                    await asyncio.wait_for(self._condition.wait(), remaining)
                except TimeoutError as error:
                    message = "smtp: timed out acquiring a connection"
                    raise SMTPTimeoutError(message) from error
        return await self._dial()

    async def _dial(self) -> AsyncSession:
        session = AsyncSession(self._config)
        try:
            await session.connect()
        except BaseException:
            await self._release_capacity()
            raise
        async with self._condition:
            if self._closed:
                self._live -= 1
                self._condition.notify()
                await session.discard()
                message = "smtp: client is closed"
                raise SMTPConnectionError(message)
            self._owned.add(session)
        return session

    async def _release_capacity(self) -> None:
        async with self._condition:
            self._live -= 1
            self._condition.notify()

    @staticmethod
    async def _is_healthy(session: AsyncSession) -> bool:
        try:
            await session.noop()
        except MxRavenError:
            return False
        return True
