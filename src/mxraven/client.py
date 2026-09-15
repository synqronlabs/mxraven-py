"""SMTP submission client.

The client maintains a bounded, thread-safe pool of authenticated connections
and is safe for concurrent use. The submission service requires STARTTLS and
SMTP AUTH, so both are always used.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

from mxraven._internal.smtp.driver import ConnectionConfig
from mxraven._internal.smtp.pool import Pool
from mxraven._internal.submission import (
    transaction_from_built,
    transaction_from_envelope,
    validate_options,
)
from mxraven.message import Envelope, Message

if TYPE_CHECKING:
    import ssl
    from collections.abc import Iterable, Sequence

    from mxraven.result import Result

__all__ = ["DEFAULT_ADDRESS_PORT", "Client"]

DEFAULT_ADDRESS_PORT = 587
_DEFAULT_POOL_SIZE = 5
_DEFAULT_CONNECT_TIMEOUT = 30.0
_DEFAULT_READ_TIMEOUT = 300.0
_DEFAULT_WRITE_TIMEOUT = 300.0


class Client:
    """Submits mail to the mxRaven SMTP submission service.

    A client maintains a bounded pool of authenticated connections. Use it as
    a context manager or call :meth:`close` to release them.

    Example:
        ```python
        client = Client(
            host="smtp.mxraven.com",
            username="mxr_tx_ab12cd34ef56",
            secret=secret,
        )
        with client:
            result = client.send(message)
        ```

    Args:
        host: The submission server host; a port must not be included.
        port: The submission server port. Defaults to ``587``.
        username: The submission API key username.
        secret: The submission API key secret.
        tls: A custom STARTTLS ``ssl.SSLContext``. The default verifies the
            server certificate against the system roots.
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
        auth_mechanisms: Preferred SASL mechanisms in order. When empty, the
            client prefers ``PLAIN`` then ``LOGIN``.

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
        self._pool = Pool(config, pool_size)

    def send(self, message: Message) -> Result:
        """Submit a composed message and return the server's result.

        Args:
            message: The message to submit. It may be sent more than once.

        Returns:
            The server's result for the submission, including the mxRaven
            `message_ref` and per-recipient acceptance.

        Raises:
            TypeError: `message` is not a :class:`Message`.
            ValueError: The message cannot be built.
            SMTPError: The server rejected a command.
            SMTPTransactionError: Every recipient was rejected; the
                per-recipient detail is on the exception's `result`.
        """
        candidate: object = message
        if not isinstance(candidate, Message):
            error = "mail: message is required"
            raise TypeError(error)
        built = message.build()
        return self._pool.send(transaction_from_built(built), built.data)

    def send_raw(
        self,
        envelope: Envelope,
        data: bytes | bytearray | memoryview | Iterable[bytes],
        *,
        prefer_bdat: bool = False,
    ) -> Result:
        """Stream an already serialized RFC 5322 message with an explicit envelope.

        The message is not parsed, so the caller is responsible for RFC 5322
        correctness. Prefer this for large or pre-rendered messages.

        Args:
            envelope: The SMTP envelope, independent of the message headers.
            data: The complete message bytes, or an iterable of chunks that is
                streamed through ``DATA``.
            prefer_bdat: Whether to use ``BDAT`` when ``CHUNKING`` is
                advertised. Streaming sources always use ``DATA``.

        Returns:
            The server's result for the submission.

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
            return self._pool.send(
                transaction_from_envelope(envelope, raw), raw, prefer_bdat=prefer_bdat
            )
        return self._pool.send(transaction_from_envelope(envelope, None), data)

    def close(self) -> None:
        """Release the pooled connections. Safe to call more than once."""
        self._pool.close()

    def __enter__(self) -> Self:
        """Return the client."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Release the pooled connections."""
        self.close()
