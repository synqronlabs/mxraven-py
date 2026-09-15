"""Synchronous SMTP transport.

Executes the sans-I/O protocol intents over a blocking socket, with optional
TLS. One connection is used sequentially; concurrency is provided by the
connection pool, not by this class.
"""

from __future__ import annotations

import contextlib
import socket
import ssl
from typing import TYPE_CHECKING

from mxraven._internal.smtp.driver import (
    ConnectionConfig,
    ReadLine,
    SessionState,
    UpgradeTls,
    Write,
    close_protocol,
    connect_protocol,
    noop_protocol,
    parse_local_address,
    send_protocol,
)
from mxraven.errors import SMTPConnectionError, SMTPProtocolError, SMTPTimeoutError

if TYPE_CHECKING:
    from collections.abc import Generator, Iterable

    from mxraven._internal.smtp.driver import Intent
    from mxraven._internal.smtp.transaction import Transaction
    from mxraven.result import Result

__all__ = ["Session"]

_MAX_LINE_LENGTH = 8192
_RECV_SIZE = 4096


class _SocketTransport:
    """Executes protocol intents on a blocking socket."""

    def __init__(self, sock: socket.socket, config: ConnectionConfig) -> None:
        self._sock = sock
        self._config = config
        self._buffer = bytearray()

    def run(self, gen: Generator[Intent, object, None]) -> None:
        """Drive a protocol generator to completion.

        Transport failures are re-raised inside the generator so the protocol
        can add context; uncaught failures propagate to the caller.
        """
        send_value: object = None
        pending: BaseException | None = None
        while True:
            try:
                intent = gen.throw(pending) if pending is not None else gen.send(send_value)
            except StopIteration:
                return
            pending = None
            try:
                send_value = self._execute(intent)
            except TimeoutError as error:
                pending = SMTPTimeoutError("smtp: timed out")
                pending.__cause__ = error
            except OSError as error:
                pending = SMTPConnectionError("smtp: connection failure")
                pending.__cause__ = error

    def close(self) -> None:
        """Close the socket, ignoring errors during cleanup."""
        with contextlib.suppress(OSError):
            self._sock.close()

    def _execute(self, intent: Intent) -> object:
        if isinstance(intent, ReadLine):
            return self._read_line(intent.timeout)
        if isinstance(intent, Write):
            self._write(intent.data)
            return None
        if isinstance(intent, UpgradeTls):
            self._upgrade(intent.context, intent.server_hostname)
            return None
        self.close()
        return None

    def _read_line(self, timeout: float | None) -> str:
        self._sock.settimeout(timeout)
        while True:
            index = self._buffer.find(b"\n")
            if index >= 0:
                raw = bytes(self._buffer[: index + 1])
                del self._buffer[: index + 1]
                return _decode_line(raw)
            if len(self._buffer) > _MAX_LINE_LENGTH:
                message = "smtp: response line too long"
                raise SMTPProtocolError(message)
            chunk = self._sock.recv(_RECV_SIZE)
            if not chunk:
                message = "smtp: connection closed by server"
                raise SMTPConnectionError(message)
            self._buffer += chunk

    def _write(self, data: bytes) -> None:
        self._sock.settimeout(self._config.write_timeout)
        self._sock.sendall(data)

    def _upgrade(self, context: ssl.SSLContext | None, server_hostname: str) -> None:
        if context is None:
            context = ssl.create_default_context()
        self._sock = context.wrap_socket(self._sock, server_hostname=server_hostname)
        self._buffer.clear()


def _decode_line(raw: bytes) -> str:
    if raw.endswith(b"\n"):
        raw = raw[:-1]
    if raw.endswith(b"\r"):
        raw = raw[:-1]
    return raw.decode("utf-8", errors="replace")


class Session:
    """A single synchronous SMTP connection.

    A session is not safe for concurrent use. :meth:`send` may be called
    repeatedly to submit several messages over the same authenticated
    connection.
    """

    def __init__(self, config: ConnectionConfig) -> None:
        """Store the connection parameters; no I/O happens until :meth:`connect`."""
        self._config = config
        self._transport: _SocketTransport | None = None
        self._state: SessionState | None = None
        self._closed = False

    def connect(self) -> None:
        """Connect, negotiate TLS, and authenticate.

        Raises:
            SMTPConnectionError: The TCP connection failed.
            SMTPTimeoutError: The connect deadline elapsed.
            SMTPError: The server rejected the greeting or authentication.
            SMTPCapabilityError: STARTTLS or AUTH is required but unavailable.
        """
        if self._closed:
            message = "smtp: client is closed"
            raise SMTPConnectionError(message)
        source = parse_local_address(self._config.local_address)
        try:
            sock = socket.create_connection(
                (self._config.host, self._config.port),
                timeout=self._config.connect_timeout,
                source_address=source,
            )
        except TimeoutError as error:
            message = "smtp: connect timed out"
            raise SMTPTimeoutError(message) from error
        except OSError as error:
            message = "smtp: connection failure"
            raise SMTPConnectionError(message) from error
        if self._config.implicit_tls:
            context = self._config.tls_context or ssl.create_default_context()
            try:
                sock = context.wrap_socket(sock, server_hostname=self._config.host)
            except OSError as error:
                sock.close()
                message = "smtp: connection failure"
                raise SMTPConnectionError(message) from error
        transport = _SocketTransport(sock, self._config)
        state = SessionState()
        try:
            transport.run(connect_protocol(state, self._config))
        except BaseException:
            transport.close()
            raise
        self._transport = transport
        self._state = state

    @property
    def state(self) -> SessionState:
        """The protocol state, populated once connected."""
        if self._state is None:
            message = "smtp: session is not connected"
            raise SMTPConnectionError(message)
        return self._state

    @property
    def is_connected(self) -> bool:
        """Whether the session has an open connection."""
        return self._transport is not None

    def send(
        self,
        transaction: Transaction,
        content: bytes | Iterable[bytes],
        *,
        prefer_bdat: bool = False,
        require_all_recipients: bool = False,
    ) -> Result:
        """Submit one message and return the server's result.

        Raises:
            SMTPError: The server rejected a command.
            SMTPTransactionError: Every recipient was rejected, or any
                recipient was rejected while `require_all_recipients` is set.
        """
        if self._transport is None or self._state is None:
            message = "smtp: session is not connected"
            raise SMTPConnectionError(message)
        self._transport.run(
            send_protocol(
                self._state,
                self._config,
                transaction,
                content,
                prefer_bdat=prefer_bdat,
                require_all_recipients=require_all_recipients,
            )
        )
        result = self._state.result
        if result is None:
            message = "smtp: transaction produced no result"
            raise SMTPProtocolError(message)
        return result

    def noop(self) -> None:
        """Send ``NOOP`` as a liveness check.

        Raises:
            SMTPError: The server rejected the command.
            SMTPConnectionError: The connection is not usable.
        """
        if self._transport is None or self._state is None:
            message = "smtp: session is not connected"
            raise SMTPConnectionError(message)
        self._transport.run(noop_protocol(self._state, self._config))

    def close(self) -> None:
        """Send a best-effort ``QUIT`` and close the connection."""
        if self._transport is not None and self._state is not None:
            try:
                self._transport.run(close_protocol(self._state, self._config))
            finally:
                self._transport.close()
                self._transport = None
                self._state = None
                self._closed = True

    def discard(self) -> None:
        """Close the connection abruptly without ``QUIT``."""
        if self._transport is not None:
            self._transport.close()
            self._transport = None
            self._state = None
        self._closed = True
