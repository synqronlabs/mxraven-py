"""Shared submission helpers for the synchronous and asynchronous clients.

Validates client options and converts public message/envelope values into the
transport-level :class:`~mxraven._internal.smtp.transaction.Transaction`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mxraven._internal.address import MailboxAddress, contains_non_ascii, parse_address
from mxraven._internal.smtp.transaction import Recipient, Transaction

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mxraven.message import BuiltMessage, Envelope

__all__ = [
    "transaction_from_built",
    "transaction_from_envelope",
    "validate_options",
]

_ASCII_LIMIT = 0x7F
_MAX_PORT = 65535


def validate_options(
    *,
    host: str,
    port: int,
    username: str,
    secret: str,
    pool_size: int,
    connect_timeout: float,
    read_timeout: float,
    write_timeout: float,
) -> None:
    """Validate client constructor options.

    Raises:
        ValueError: An option is missing or invalid.
    """
    if not host.strip():
        message = "mail: server host is required"
        raise ValueError(message)
    if not 0 < port <= _MAX_PORT:
        message = f"mail: invalid server port {port}"
        raise ValueError(message)
    if not username.strip():
        message = "mail: username must not be empty"
        raise ValueError(message)
    if secret == "":
        message = "mail: secret must not be empty"
        raise ValueError(message)
    if pool_size <= 0:
        message = f"mail: invalid pool size {pool_size}"
        raise ValueError(message)
    for value, name in (
        (connect_timeout, "connect timeout"),
        (read_timeout, "read timeout"),
        (write_timeout, "write timeout"),
    ):
        if value <= 0:
            message = f"mail: {name} must be positive"
            raise ValueError(message)


def transaction_from_built(built: BuiltMessage) -> Transaction:
    """Convert a serialized message into a transport transaction."""
    sender = parse_address(built.sender) if built.sender else None
    return Transaction(
        sender=sender,
        recipients=tuple(Recipient(parse_address(address)) for address in built.recipients),
        size=built.size,
        body_type=built.body_type,
        smtp_utf8=built.smtp_utf8,
    )


def transaction_from_envelope(envelope: Envelope, data: bytes | None) -> Transaction:
    """Convert a raw envelope and message bytes into a transport transaction.

    Args:
        envelope: The SMTP envelope.
        data: The complete message bytes, or `None` when the message is streamed
            and its size and body type are unknown.

    Raises:
        ValueError: The envelope has no recipients or an address is invalid.
    """
    sender = parse_address(envelope.sender) if envelope.sender.strip() else None
    recipients = tuple(Recipient(parse_address(address)) for address in envelope.recipients)
    if not recipients:
        message = "mail: envelope requires at least one recipient"
        raise ValueError(message)
    smtp_utf8 = _addresses_need_utf8(sender, recipients)
    body_type = ""
    size = 0
    if data is not None:
        size = len(data)
        body_type = "8BITMIME" if any(byte > _ASCII_LIMIT for byte in data) else ""
    return Transaction(
        sender=sender,
        recipients=recipients,
        size=size,
        body_type=body_type,
        smtp_utf8=smtp_utf8,
    )


def _addresses_need_utf8(sender: MailboxAddress | None, recipients: Sequence[Recipient]) -> bool:
    candidates: list[MailboxAddress] = [recipient.address for recipient in recipients]
    if sender is not None:
        candidates.append(sender)
    return any(
        contains_non_ascii(address.local_part) or contains_non_ascii(address.domain)
        for address in candidates
    )
