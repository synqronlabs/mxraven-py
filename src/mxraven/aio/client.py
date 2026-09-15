"""Asynchronous SMTP submission client."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, cast

from mxraven._internal.aio.pool import AsyncPool
from mxraven._internal.smtp.driver import ConnectionConfig
from mxraven._internal.submission import (
    transaction_from_built,
    transaction_from_envelope,
    validate_options,
)
from mxraven.client import (
    _DEFAULT_CONNECT_TIMEOUT,
    _DEFAULT_POOL_SIZE,
    _DEFAULT_READ_TIMEOUT,
    _DEFAULT_WRITE_TIMEOUT,
    DEFAULT_ADDRESS_PORT,
)
from mxraven.message import Envelope, Message

if TYPE_CHECKING:
    import ssl
    from collections.abc import AsyncIterable, Iterable, Sequence

    from mxraven.result import Result

__all__ = ["Client"]


class Client:
    """Submits mail to the mxRaven SMTP submission service using asyncio.

    A client maintains a bounded pool of authenticated connections. Use it as
    an asynchronous context manager or call :meth:`close`.

    Example:
        ```python
        async with Client(host="smtp.mxraven.com", username=user, secret=secret) as client:
            result = await client.send(message)
        ```

    Args:
        host: The submission server host; a port must not be included.
        port: The submission server port. Defaults to ``587``.
        username: The submission API key username.
        secret: The submission API key secret.
        tls: A custom STARTTLS ``ssl.SSLContext``.
        pool_size: The maximum number of pooled connections. Defaults to ``5``.
        connect_timeout: The TCP connect timeout in seconds.
        read_timeout: The per-reply read timeout in seconds.
        write_timeout: The per-write timeout in seconds.
        local_name: The name sent in ``EHLO``. Defaults to ``localhost``.
        starttls: Whether to issue ``STARTTLS`` when advertised.
        require_tls: Whether to fail when ``STARTTLS`` is not advertised.
        implicit_tls: Whether the connection uses TLS from the first byte
            (SMTPS, typically port 465). When true, ``STARTTLS`` is not issued.
        local_address: An optional local bind address, ``host`` or
            ``host:port``.
        auth_mechanisms: Preferred SASL mechanisms in order.

    Raises:
        ValueError: A required argument is missing or invalid.
    """

    def __init__(
        self,
        host: str,
        *,
        port: int = DEFAULT_ADDRESS_PORT,
        username: str,
        secret: str,
        tls: ssl.SSLContext | None = None,
        pool_size: int = _DEFAULT_POOL_SIZE,
        connect_timeout: float = _DEFAULT_CONNECT_TIMEOUT,
        read_timeout: float = _DEFAULT_READ_TIMEOUT,
        write_timeout: float = _DEFAULT_WRITE_TIMEOUT,
        local_name: str | None = None,
        starttls: bool = True,
        require_tls: bool = True,
        implicit_tls: bool = False,
        local_address: str | None = None,
        auth_mechanisms: Sequence[str] = (),
    ) -> None:
        """Initialize the client and its connection pool."""
        validate_options(
            host=host,
            port=port,
            username=username,
            secret=secret,
            pool_size=pool_size,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            write_timeout=write_timeout,
        )
        config = ConnectionConfig(
            host=host,
            port=port,
            local_name=local_name if local_name else "localhost",
            username=username,
            password=secret,
            tls_context=tls,
            starttls=starttls,
            require_tls=require_tls,
            implicit_tls=implicit_tls,
            local_address=local_address,
            auth_mechanisms=tuple(auth_mechanisms),
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            write_timeout=write_timeout,
        )
        self._pool = AsyncPool(config, pool_size)

    async def send(self, message: Message) -> Result:
        """Submit a composed message and return the server's result.

        Raises:
            TypeError: `message` is not a :class:`Message`.
            ValueError: The message cannot be built.
            SMTPError: The server rejected a command.
            SMTPTransactionError: Every recipient was rejected.
        """
        candidate: object = message
        if not isinstance(candidate, Message):
            error = "mail: message is required"
            raise TypeError(error)
        built = message.build()
        return await self._pool.send(transaction_from_built(built), built.data)

    async def send_raw(
        self,
        envelope: Envelope,
        data: bytes | bytearray | memoryview | Iterable[bytes] | AsyncIterable[bytes],
        *,
        prefer_bdat: bool = False,
    ) -> Result:
        """Stream an already serialized RFC 5322 message with an explicit envelope.

        Raises:
            TypeError: `envelope` is not an :class:`Envelope`.
            ValueError: The envelope has no recipients or an address is
                invalid.
            SMTPError: The server rejected a command.
            SMTPTransactionError: Every recipient was rejected.
        """
        candidate: object = envelope
        if not isinstance(candidate, Envelope):
            error = "mail: envelope is required"
            raise TypeError(error)
        if isinstance(data, bytes | bytearray | memoryview):
            raw = bytes(data)
            return await self._pool.send(
                transaction_from_envelope(envelope, raw), raw, prefer_bdat=prefer_bdat
            )
        if hasattr(data, "__aiter__"):
            collected = await _collect(cast("AsyncIterable[bytes]", data))
            return await self._pool.send(
                transaction_from_envelope(envelope, collected), collected, prefer_bdat=prefer_bdat
            )
        return await self._pool.send(transaction_from_envelope(envelope, None), data)

    async def close(self) -> None:
        """Release the pooled connections. Safe to call more than once."""
        await self._pool.close()

    async def __aenter__(self) -> Self:
        """Return the client."""
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        """Release the pooled connections."""
        await self.close()


async def _collect(chunks: AsyncIterable[bytes]) -> bytes:
    out = bytearray()
    async for chunk in chunks:
        out += chunk
    return bytes(out)
