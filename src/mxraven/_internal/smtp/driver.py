"""Sans-I/O SMTP protocol driver.

The protocol is expressed as generators that yield :data:`Intent` values and
receive the result of each intent back. A runner (one for blocking sockets, one
for asyncio streams) executes the intents. This keeps greeting handling,
STARTTLS ordering, authentication, pipelining, DATA/BDAT framing, and the
all-recipients-rejected path in exactly one place.

The protocol never touches a socket, and this module is shared by the
synchronous and asynchronous clients.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mxraven._internal.address import mailbox_to_string
from mxraven._internal.smtp.auth import encode_login, encode_plain, select_mechanism
from mxraven._internal.smtp.extensions import Capabilities, capabilities_from, parse_extensions
from mxraven._internal.smtp.reply import Reply, parse_reply_line, reply_from_lines
from mxraven._internal.smtp.transaction import (
    Recipient,
    Transaction,
    build_mail_from,
    build_rcpt_to,
    dot_stuff,
    dot_stuff_chunk,
)
from mxraven.errors import (
    MxRavenError,
    SMTPCapabilityError,
    SMTPConnectionError,
    SMTPProtocolError,
    SMTPTransactionError,
)
from mxraven.result import RecipientResult, Result, parse_message_ref

if TYPE_CHECKING:
    import ssl
    from collections.abc import Generator, Iterable

    Content = bytes | Iterable[bytes]

__all__ = [
    "Close",
    "ConnectionConfig",
    "Intent",
    "ReadLine",
    "SessionState",
    "UpgradeTls",
    "Write",
    "close_protocol",
    "connect_protocol",
    "noop_protocol",
    "send_protocol",
]

_WRITE_CHUNK = 64 * 1024
_TRAILING_BYTES = 2
_AUTO_BDAT_THRESHOLD = 1024 * 1024
_DEFAULT_BDAT_CHUNK = 64 * 1024
_CRLF = "\r\n"
_AUTH_PLAIN = "PLAIN"
_AUTH_LOGIN = "LOGIN"
_LOGIN_CHALLENGE = 334


@dataclass(frozen=True, slots=True)
class ReadLine:
    """Read one reply line, applying an absolute read deadline."""

    timeout: float | None


@dataclass(frozen=True, slots=True)
class Write:
    """Write raw bytes to the connection."""

    data: bytes


@dataclass(frozen=True, slots=True)
class UpgradeTls:
    """Upgrade the connection to TLS using STARTTLS."""

    server_hostname: str
    context: ssl.SSLContext | None


@dataclass(frozen=True, slots=True)
class Close:
    """Close the connection."""


Intent = ReadLine | Write | UpgradeTls | Close


@dataclass(frozen=True, slots=True)
class ConnectionConfig:
    """Connection parameters shared by both transports.

    Attributes:
        host: The submission server host.
        port: The submission server port.
        local_name: The name sent in ``EHLO``.
        username: The submission API key username.
        password: The submission API key secret.
        tls_context: The STARTTLS context; a verifying default is used when
            `None`.
        starttls: Whether to issue ``STARTTLS`` when advertised.
        require_tls: Whether to fail when ``STARTTLS`` is not advertised.
        connect_timeout: The TCP connect timeout in seconds.
        read_timeout: The per-reply read timeout in seconds.
        write_timeout: The per-write timeout in seconds.
    """

    host: str
    port: int
    local_name: str = "localhost"
    username: str = ""
    password: str = ""
    tls_context: ssl.SSLContext | None = None
    starttls: bool = True
    require_tls: bool = True
    implicit_tls: bool = False
    local_address: str | None = None
    auth_mechanisms: tuple[str, ...] = ()
    connect_timeout: float = 30.0
    read_timeout: float = 300.0
    write_timeout: float = 300.0


@dataclass
class SessionState:
    """Mutable protocol state for one connection.

    Attributes:
        capabilities: The advertised capabilities, once ``EHLO``/``HELO`` ran.
        is_tls: Whether the connection is encrypted.
        authenticated: Whether authentication succeeded.
        greeting: The server greeting text.
        last_reply: The most recently read reply.
        recipient_results: Per-recipient outcomes of the current transaction.
        accepted_count: The number of accepted recipients.
        result: The result of the current transaction.
        closed: Whether the connection was closed by the protocol.
    """

    capabilities: Capabilities | None = None
    is_tls: bool = False
    authenticated: bool = False
    greeting: str = ""
    last_reply: Reply | None = None
    recipient_results: list[RecipientResult] | None = None
    accepted_count: int = 0
    result: Result | None = None
    closed: bool = False


def connect_protocol(
    state: SessionState, config: ConnectionConfig
) -> Generator[Intent, object, None]:
    """Run the connection setup sequence.

    Reads the greeting, negotiates ``EHLO``/``HELO``, upgrades via ``STARTTLS``
    when required, and authenticates when credentials are configured.

    Args:
        state: The session state to populate.
        config: The connection parameters.

    Raises:
        SMTPError: The server rejected the greeting or an authentication reply.
        SMTPCapabilityError: ``STARTTLS`` or ``AUTH`` is required but missing.
    """
    state.is_tls = config.implicit_tls
    yield from _read_reply(state, config)
    greeting = _reply(state)
    if not greeting.is_success:
        raise _error(greeting)
    state.greeting = greeting.message

    yield from _hello(state, config)
    yield from _maybe_starttls(state, config)
    yield from _authenticate(state, config)


def send_protocol(
    state: SessionState,
    config: ConnectionConfig,
    transaction: Transaction,
    content: Content,
    *,
    prefer_bdat: bool = False,
    bdat_chunk_size: int = _DEFAULT_BDAT_CHUNK,
    require_all_recipients: bool = False,
) -> Generator[Intent, object, None]:
    """Run one mail transaction and populate ``state.result``.

    Args:
        state: The session state; its capabilities must be populated.
        config: The connection parameters.
        transaction: The envelope to submit.
        content: The serialized RFC 5322 message, either as complete bytes or
            as an iterable of chunks that is streamed through ``DATA``.
        prefer_bdat: Whether to force ``BDAT`` when ``CHUNKING`` is advertised.
        bdat_chunk_size: The ``BDAT`` chunk size in bytes.
        require_all_recipients: Whether to abort and ``RSET`` when any
            recipient is rejected.

    Raises:
        SMTPError: The server rejected a command.
        SMTPTransactionError: Every recipient was rejected.
    """
    caps = _capabilities(state)
    mail_command = build_mail_from(caps, transaction, is_tls=state.is_tls)
    rcpt_commands = [
        build_rcpt_to(caps, recipient, smtp_utf8=transaction.smtp_utf8)
        for recipient in transaction.recipients
    ]
    if not rcpt_commands:
        message = "mail: envelope requires at least one recipient"
        raise ValueError(message)

    state.recipient_results = []
    state.accepted_count = 0
    if caps.pipelining:
        yield from _envelope_pipelined(
            state,
            config,
            mail_command,
            rcpt_commands,
            transaction,
            require_all_recipients=require_all_recipients,
        )
    else:
        yield from _envelope_sequential(
            state,
            config,
            mail_command,
            rcpt_commands,
            transaction,
            require_all_recipients=require_all_recipients,
        )

    if state.accepted_count == 0:
        yield from _best_effort_rset(state, config)
        message = "smtp: transaction failed: all recipients rejected"
        raise SMTPTransactionError(message, _to_result(state, 0, ""))

    if not isinstance(content, bytes | bytearray | memoryview):
        yield from _send_data_stream(state, config, content)
        final = _reply(state)
        state.result = _to_result(state, final.code, final.message)
        return

    payload = bytes(content)
    use_bdat = caps.chunking and (prefer_bdat or len(payload) > _AUTO_BDAT_THRESHOLD)
    if use_bdat:
        if prefer_bdat:
            yield from _send_bdat(state, config, payload, bdat_chunk_size)
        else:
            yield from _send_bdat_single(state, config, payload)
        state.result = _to_result(state, 0, "")
        return

    yield from _send_data(state, config, payload)
    final = _reply(state)
    state.result = _to_result(state, final.code, final.message)


def noop_protocol(state: SessionState, config: ConnectionConfig) -> Generator[Intent, object, None]:
    """Issue ``NOOP`` as a connection liveness check."""
    yield from _command(state, config, "NOOP")
    if not _reply(state).is_success:
        raise _error(_reply(state))


def close_protocol(
    state: SessionState, config: ConnectionConfig
) -> Generator[Intent, object, None]:
    """Send a best-effort ``QUIT`` and close the connection."""
    if not state.closed:
        with contextlib.suppress(MxRavenError):
            yield from _command(state, config, "QUIT")
        state.closed = True
    yield Close()


def _hello(state: SessionState, config: ConnectionConfig) -> Generator[Intent, object, None]:
    yield from _command(state, config, f"EHLO {config.local_name}")
    reply = _reply(state)
    if reply.is_success:
        hostname = reply.lines[0].split(" ")[0] if reply.lines else ""
        state.capabilities = capabilities_from(
            parse_extensions(list(reply.lines)), is_esmtp=True, hostname=hostname
        )
        return
    yield from _command(state, config, f"HELO {config.local_name}")
    if not _reply(state).is_success:
        raise _error(_reply(state))
    state.capabilities = capabilities_from({}, is_esmtp=False, hostname="")


def _maybe_starttls(
    state: SessionState, config: ConnectionConfig
) -> Generator[Intent, object, None]:
    if config.implicit_tls or not config.starttls or state.is_tls:
        return
    caps = _capabilities(state)
    if caps.starttls:
        yield from _command(state, config, "STARTTLS")
        if not _reply(state).is_success:
            raise _error(_reply(state))
        yield UpgradeTls(config.host, config.tls_context)
        state.is_tls = True
        state.capabilities = None
        yield from _hello(state, config)
    elif config.require_tls:
        message = "smtp: STARTTLS not supported by server"
        raise SMTPCapabilityError(message)


def _authenticate(state: SessionState, config: ConnectionConfig) -> Generator[Intent, object, None]:
    if not config.username and not config.password:
        return
    caps = _capabilities(state)
    if not caps.has("AUTH"):
        message = "smtp: AUTH not supported by server"
        raise SMTPCapabilityError(message)
    mechanism = select_mechanism(config.auth_mechanisms, caps.auth_mechanisms)
    if mechanism == _AUTH_PLAIN:
        yield from _auth_plain(state, config)
    elif mechanism == _AUTH_LOGIN:
        yield from _auth_login(state, config)
    else:
        message = "smtp: no supported authentication mechanism available"
        raise SMTPCapabilityError(message)


def parse_local_address(value: str | None) -> tuple[str, int] | None:
    """Parse a local bind address of the form ``host`` or ``host:port``.

    Args:
        value: The configured local address, or `None`.

    Returns:
        A ``(host, port)`` pair, or `None` when `value` is empty.

    Raises:
        ValueError: The port is not a valid integer.
    """
    if value is None or not value.strip():
        return None
    address = value.strip()
    if address.startswith("["):
        end = address.find("]")
        host = address[1:end]
        remainder = address[end + 1 :]
        port = int(remainder[1:]) if remainder.startswith(":") else 0
        return host, port
    host, separator, port_text = address.rpartition(":")
    if separator and port_text.isdigit():
        return host, int(port_text)
    return address, 0


def _auth_plain(state: SessionState, config: ConnectionConfig) -> Generator[Intent, object, None]:
    encoded = encode_plain(config.username, config.password)
    yield from _command(state, config, f"AUTH PLAIN {encoded}")
    if not _reply(state).is_success:
        raise _error(_reply(state))
    state.authenticated = True


def _auth_login(state: SessionState, config: ConnectionConfig) -> Generator[Intent, object, None]:
    yield from _command(state, config, "AUTH LOGIN")
    if _reply(state).code != _LOGIN_CHALLENGE:
        raise _error(_reply(state))
    yield from _command(state, config, encode_login(config.username))
    if _reply(state).code != _LOGIN_CHALLENGE:
        raise _error(_reply(state))
    yield from _command(state, config, encode_login(config.password))
    if not _reply(state).is_success:
        raise _error(_reply(state))
    state.authenticated = True


def _envelope_sequential(
    state: SessionState,
    config: ConnectionConfig,
    mail_command: str,
    rcpt_commands: list[str],
    transaction: Transaction,
    *,
    require_all_recipients: bool,
) -> Generator[Intent, object, None]:
    yield from _command(state, config, mail_command)
    if not _reply(state).is_success:
        raise _error(_reply(state))
    for recipient, command in zip(transaction.recipients, rcpt_commands, strict=True):
        yield from _command(state, config, command)
        _record_recipient(state, recipient, _reply(state))
        results = state.recipient_results
        if require_all_recipients and results is not None and not results[-1].accepted:
            yield from _reject_all_recipients(state, config)


def _envelope_pipelined(
    state: SessionState,
    config: ConnectionConfig,
    mail_command: str,
    rcpt_commands: list[str],
    transaction: Transaction,
    *,
    require_all_recipients: bool,
) -> Generator[Intent, object, None]:
    block = _CRLF.join([mail_command, *rcpt_commands]) + _CRLF
    yield Write(block.encode("utf-8"))
    yield from _read_reply(state, config)
    mail_reply = _reply(state)
    for recipient, _command_text in zip(transaction.recipients, rcpt_commands, strict=True):
        yield from _read_reply(state, config)
        _record_recipient(state, recipient, _reply(state))
    if not mail_reply.is_success:
        raise _error(mail_reply)
    if require_all_recipients and any(
        not result.accepted for result in (state.recipient_results or ())
    ):
        yield from _reject_all_recipients(state, config)


def _reject_all_recipients(
    state: SessionState, config: ConnectionConfig
) -> Generator[Intent, object, None]:
    yield from _best_effort_rset(state, config)
    message = "smtp: transaction failed: a recipient was rejected"
    raise SMTPTransactionError(message, _to_result(state, 0, ""))


def _record_recipient(state: SessionState, recipient: Recipient, reply: Reply) -> None:
    results = state.recipient_results
    if results is None:
        message = "smtp: transaction is not active"
        raise SMTPConnectionError(message)
    accepted = reply.is_success
    error = None if accepted else reply.to_error()
    results.append(
        RecipientResult(
            address=mailbox_to_string(recipient.address),
            accepted=accepted,
            error=error,
        )
    )
    if accepted:
        state.accepted_count += 1


def _best_effort_rset(
    state: SessionState, config: ConnectionConfig
) -> Generator[Intent, object, None]:
    try:
        yield from _command(state, config, "RSET")
    except MxRavenError:
        return


def _send_data(
    state: SessionState, config: ConnectionConfig, content: bytes
) -> Generator[Intent, object, None]:
    yield from _command(state, config, "DATA")
    if not _reply(state).is_intermediate:
        raise _error(_reply(state))
    stuffed = dot_stuff(content)
    if stuffed and not stuffed.endswith(b"\r\n"):
        stuffed += b"\r\n"
    for start in range(0, len(stuffed), _WRITE_CHUNK):
        yield Write(stuffed[start : start + _WRITE_CHUNK])
    yield Write(b".\r\n")
    yield from _read_reply(state, config)
    if not _reply(state).is_success:
        raise _error(_reply(state))


def _send_data_stream(
    state: SessionState, config: ConnectionConfig, content: Iterable[bytes]
) -> Generator[Intent, object, None]:
    yield from _command(state, config, "DATA")
    if not _reply(state).is_intermediate:
        raise _error(_reply(state))
    at_line_start = True
    tail = b""
    wrote = False
    for chunk in content:
        stuffed, at_line_start = dot_stuff_chunk(bytes(chunk), at_line_start=at_line_start)
        if stuffed:
            yield Write(stuffed)
            wrote = True
            tail = (tail + stuffed)[-_TRAILING_BYTES:]
    if wrote and not tail.endswith(b"\r\n"):
        yield Write(b"\r\n")
    yield Write(b".\r\n")
    yield from _read_reply(state, config)
    if not _reply(state).is_success:
        raise _error(_reply(state))


def _send_bdat_single(
    state: SessionState, config: ConnectionConfig, content: bytes
) -> Generator[Intent, object, None]:
    if not content:
        yield from _command(state, config, "BDAT 0 LAST")
        if not _reply(state).is_success:
            raise _error(_reply(state))
        return
    yield from _send_line(f"BDAT {len(content)} LAST")
    yield Write(content)
    yield from _read_reply(state, config)
    if not _reply(state).is_success:
        raise _error(_reply(state))


def _send_bdat(
    state: SessionState, config: ConnectionConfig, content: bytes, chunk_size: int
) -> Generator[Intent, object, None]:
    if chunk_size <= 0:
        message = "smtp: BDAT chunk size must be positive"
        raise ValueError(message)
    if not content:
        yield from _command(state, config, "BDAT 0 LAST")
        if not _reply(state).is_success:
            raise _error(_reply(state))
        return
    offset = 0
    total = len(content)
    while offset < total:
        chunk = content[offset : offset + chunk_size]
        offset += len(chunk)
        last = offset >= total
        command = f"BDAT {len(chunk)}" + (" LAST" if last else "")
        yield from _send_line(command)
        yield Write(chunk)
        yield from _read_reply(state, config)
        if not _reply(state).is_success:
            raise _error(_reply(state))


def _to_result(state: SessionState, code: int, message: str) -> Result:
    return Result(
        message_ref=parse_message_ref(message),
        code=code,
        message=message,
        recipients=tuple(state.recipient_results or ()),
    )


def _send_line(command: str) -> Generator[Intent, object, None]:
    yield Write((command + _CRLF).encode("utf-8"))


def _command(
    state: SessionState, config: ConnectionConfig, command: str
) -> Generator[Intent, object, None]:
    yield from _send_line(command)
    yield from _read_reply(state, config)


def _read_reply(state: SessionState, config: ConnectionConfig) -> Generator[Intent, object, None]:
    raws: list[str] = []
    while True:
        raw = yield ReadLine(config.read_timeout)
        if not isinstance(raw, str):
            message = "smtp: malformed transport result"
            raise SMTPProtocolError(message)
        raws.append(raw)
        _code, _message, final = parse_reply_line(raw)
        if final:
            break
    state.last_reply = reply_from_lines(raws)


def _reply(state: SessionState) -> Reply:
    reply = state.last_reply
    if reply is None:
        message = "smtp: no reply available"
        raise SMTPConnectionError(message)
    return reply


def _capabilities(state: SessionState) -> Capabilities:
    caps = state.capabilities
    if caps is None:
        message = "smtp: session is not initialized"
        raise SMTPConnectionError(message)
    return caps


def _error(reply: Reply) -> MxRavenError:
    error = reply.to_error()
    if error is None:
        return SMTPConnectionError(f"smtp: unexpected reply {reply.code}")
    return error
