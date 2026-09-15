"""Asynchronous SMTP transport.

Executes the shared sans-I/O protocol intents over :mod:`asyncio` streams.
"""

from __future__ import annotations

import asyncio
import contextlib
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

__all__ = ["AsyncSession"]

_MAX_LINE_LENGTH = 8192


class _StreamTransport:
    """Executes protocol intents on asyncio streams."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        config: ConnectionConfig,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._config = config

    async def run(self, gen: Generator[Intent, object, None]) -> None:
        """Drive a protocol generator to completion."""
        send_value: object = None
        pending: BaseException | None = None
        while True:
            try:
                intent = gen.throw(pending) if pending is not None else gen.send(send_value)
            except StopIteration:
                return
            pending = None
            try:
                send_value = await self._execute(intent)
            except TimeoutError as error:
                pending = SMTPTimeoutError("smtp: timed out")
                pending.__cause__ = error
            except OSError as error:
                pending = SMTPConnectionError("smtp: connection failure")
                pending.__cause__ = error

    async def close(self) -> None:
        """Close the writer, ignoring errors during cleanup."""
        self._writer.close()
        with contextlib.suppress(Exception):
            await self._writer.wait_closed()

    async def _execute(self, intent: Intent) -> object:
        if isinstance(intent, ReadLine):
            return await self._read_line(intent.timeout)
        if isinstance(intent, Write):
            await self._write(intent.data)
            return None
        if isinstance(intent, UpgradeTls):
            await self._upgrade(intent.context, intent.server_hostname)
            return None
        await self.close()
        return None

    async def _read_line(self, timeout: float | None) -> str:
        raw = await asyncio.wait_for(self._reader.readline(), timeout)
        if not raw:
            message = "smtp: connection closed by server"
            raise SMTPConnectionError(message)
        if len(raw) > _MAX_LINE_LENGTH:
            message = "smtp: response line too long"
            raise SMTPProtocolError(message)
        if raw.endswith(b"\n"):
            raw = raw[:-1]
        if raw.endswith(b"\r"):
            raw = raw[:-1]
        return raw.decode("utf-8", errors="replace")

    async def _write(self, data: bytes) -> None:
        self._writer.write(data)
        await asyncio.wait_for(self._writer.drain(), self._config.write_timeout)

    async def _upgrade(self, context: ssl.SSLContext | None, server_hostname: str) -> None:
        if context is None:
            context = ssl.create_default_context()
        await self._writer.start_tls(context, server_hostname=server_hostname)


class AsyncSession:
    """A single asynchronous SMTP connection.

    A session is not safe for concurrent use; concurrency is provided by the
    pool.
    """

    def __init__(self, config: ConnectionConfig) -> None:
        """Store the connection parameters; no I/O happens until :meth:`connect`."""
        self._config = config
        self._transport: _StreamTransport | None = None
        self._state: SessionState | None = None
        self._closed = False

    async def connect(self) -> None:
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
        context: ssl.SSLContext | None = None
        if self._config.implicit_tls:
            context = self._config.tls_context or ssl.create_default_context()
        source = parse_local_address(self._config.local_address)
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    self._config.host,
                    self._config.port,
                    ssl=context,
                    server_hostname=self._config.host if context is not None else None,
                    local_addr=source,
                ),
                self._config.connect_timeout,
            )
        except TimeoutError as error:
            message = "smtp: connect timed out"
            raise SMTPTimeoutError(message) from error
        except OSError as error:
            message = "smtp: connection failure"
            raise SMTPConnectionError(message) from error
        transport = _StreamTransport(reader, writer, self._config)
        state = SessionState()
        try:
            await transport.run(connect_protocol(state, self._config))
        except BaseException:
            await transport.close()
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

    async def send(
        self,
        transaction: Transaction,
        content: bytes | Iterable[bytes],
        *,
        prefer_bdat: bool = False,
        require_all_recipients: bool = False,
    ) -> Result:
        """Submit one message and return the server's result."""
        if self._transport is None or self._state is None:
            message = "smtp: session is not connected"
            raise SMTPConnectionError(message)
        await self._transport.run(
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

    async def noop(self) -> None:
        """Send ``NOOP`` as a liveness check."""
        if self._transport is None or self._state is None:
            message = "smtp: session is not connected"
            raise SMTPConnectionError(message)
        await self._transport.run(noop_protocol(self._state, self._config))

    async def close(self) -> None:
        """Send a best-effort ``QUIT`` and close the connection."""
        if self._transport is not None and self._state is not None:
            try:
                await self._transport.run(close_protocol(self._state, self._config))
            finally:
                await self._transport.close()
                self._transport = None
                self._state = None
                self._closed = True

    async def discard(self) -> None:
        """Close the connection abruptly without ``QUIT``."""
        if self._transport is not None:
            await self._transport.close()
            self._transport = None
            self._state = None
        self._closed = True
