"""SMTP envelope command construction.

Builds ``MAIL FROM`` and ``RCPT TO`` commands with the full set of parameters
the reference client supports: ``SIZE``, ``BODY``, ``SMTPUTF8``, ``REQUIRETLS``,
``BY`` (DELIVERBY), ``AUTH``, and the RFC 3461 DSN parameters ``RET``,
``ENVID``, ``NOTIFY``, and ``ORCPT``.

Every command is validated before it is returned, so a caller can build the
entire envelope and fail before writing anything to the wire.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mxraven._internal.address import MailboxAddress, mailbox_to_string
from mxraven._internal.smtp.dsn import (
    format_envelope_id,
    format_original_recipient,
    normalize_notify,
    normalize_return,
)
from mxraven.errors import SMTPCapabilityError, SMTPError

if TYPE_CHECKING:
    from mxraven._internal.smtp.extensions import Capabilities
    from mxraven.message import DeliveryBy

__all__ = [
    "Recipient",
    "RecipientDSN",
    "Transaction",
    "build_mail_from",
    "build_rcpt_to",
    "dot_stuff",
    "dot_stuff_chunk",
    "extract_message_id",
]

_MODE_NOTIFY = "N"
_MODE_RETURN = "R"
_BODY_8BIT = "8BITMIME"
_BODY_BINARY = "BINARYMIME"
_REQUIRETLS_ENHANCED = "5.7.30"
_DOT = 0x2E
_LINE_FEED = 0x0A


@dataclass(frozen=True, slots=True)
class RecipientDSN:
    """Per-recipient DSN parameters.

    Attributes:
        notify: ``NOTIFY`` values; one of ``NEVER``, ``SUCCESS``, ``FAILURE``,
            ``DELAY``. ``NEVER`` must appear alone.
        orcpt: A pre-formatted ``address-type;address`` original recipient.
        original_recipient: An ``(address_type, address)`` pair, an alternative
            to `orcpt`.
    """

    notify: tuple[str, ...] = ()
    orcpt: str = ""
    original_recipient: tuple[str, str] | None = None


@dataclass(frozen=True, slots=True)
class Recipient:
    """An envelope recipient.

    Attributes:
        address: The recipient address.
        dsn: Per-recipient DSN parameters, when the server advertises ``DSN``.
    """

    address: MailboxAddress
    dsn: RecipientDSN | None = None


@dataclass(frozen=True, slots=True)
class Transaction:
    """The full envelope for one SMTP transaction.

    Attributes:
        sender: The envelope sender, or `None` for a null reverse-path.
        recipients: At least one envelope recipient.
        size: The message size for the ``SIZE`` parameter, when non-zero.
        body_type: ``8BITMIME`` or ``BINARYMIME``, when the server advertises it.
        smtp_utf8: Whether to request the ``SMTPUTF8`` extension.
        require_tls: Whether to request ``REQUIRETLS`` (RFC 8689).
        delivery_by: The ``DELIVERBY`` request (RFC 2852).
        env_id: The DSN envelope identifier.
        dsn_ret: ``FULL`` or ``HDRS`` for the ``RET`` parameter.
        auth: The RFC 4954 ``AUTH`` identity for the reverse path.
        extension_params: Additional ``NAME`` or ``(NAME, VALUE)`` parameters,
            in the order they should be sent.
    """

    sender: MailboxAddress | None
    recipients: tuple[Recipient, ...]
    size: int = 0
    body_type: str = ""
    smtp_utf8: bool = False
    require_tls: bool = False
    delivery_by: DeliveryBy | None = None
    env_id: str = ""
    dsn_ret: str = ""
    auth: str = ""
    extension_params: tuple[tuple[str, str], ...] = ()


def build_mail_from(caps: Capabilities, transaction: Transaction, *, is_tls: bool) -> str:
    """Build a ``MAIL FROM`` command for a transaction.

    Args:
        caps: The server capabilities.
        transaction: The transaction envelope.
        is_tls: Whether the session is currently encrypted.

    Returns:
        The complete command, without a line terminator.

    Raises:
        ValueError: A parameter value is invalid.
        SMTPError: ``REQUIRETLS`` was requested but cannot be satisfied.
        SMTPCapabilityError: ``DELIVERBY`` was requested but is not advertised.
    """
    params: list[str] = []

    if caps.has("SIZE") and transaction.size > 0:
        params.append(f"SIZE={transaction.size}")

    _append_body_type(params, caps, transaction.body_type)
    if transaction.smtp_utf8 and caps.smtp_utf8:
        params.append("SMTPUTF8")

    _append_require_tls(params, caps, transaction, is_tls=is_tls)
    _append_delivery_by(params, caps, transaction.delivery_by)

    if transaction.auth:
        params.append(f"AUTH=<{transaction.auth}>")
    if transaction.dsn_ret and caps.dsn:
        params.append(f"RET={normalize_return(transaction.dsn_ret)}")
    if transaction.env_id and caps.dsn:
        params.append(f"ENVID={format_envelope_id(transaction.env_id)}")

    for name, value in transaction.extension_params:
        if transaction.delivery_by is not None and name.upper() == "BY":
            continue
        params.append(name if value == "" else f"{name}={value}")

    return _command("MAIL FROM", transaction.sender, params)


def build_rcpt_to(caps: Capabilities, recipient: Recipient, *, smtp_utf8: bool) -> str:
    """Build a ``RCPT TO`` command for one recipient.

    Args:
        caps: The server capabilities.
        recipient: The recipient and its optional DSN parameters.
        smtp_utf8: Whether the transaction negotiated ``SMTPUTF8``.

    Returns:
        The complete command, without a line terminator.

    Raises:
        ValueError: A DSN parameter is invalid.
    """
    params: list[str] = []
    dsn = recipient.dsn
    if dsn is not None and caps.dsn:
        if dsn.notify:
            params.append(f"NOTIFY={','.join(normalize_notify(list(dsn.notify)))}")
        orcpt = _format_orcpt(dsn, smtp_utf8=smtp_utf8)
        if orcpt:
            params.append(f"ORCPT={orcpt}")

    return _command("RCPT TO", recipient.address, params)


def dot_stuff_chunk(data: bytes, *, at_line_start: bool) -> tuple[bytes, bool]:
    """Dot-stuff one chunk, carrying line-start state across chunks.

    Args:
        data: The chunk bytes.
        at_line_start: Whether the chunk begins at the start of a line.

    Returns:
        The stuffed chunk and the line-start state after it.
    """
    out = bytearray()
    for byte in data:
        if at_line_start and byte == _DOT:
            out.append(_DOT)
        out.append(byte)
        at_line_start = byte == _LINE_FEED
    return bytes(out), at_line_start


def dot_stuff(data: bytes) -> bytes:
    """Apply SMTP dot-stuffing.

    A period at the start of a line is doubled. Line endings are not
    normalized, and no trailing ``CRLF`` is added: the caller is responsible
    for both, as the transaction framing appends the terminator separately.

    Args:
        data: The raw message bytes.

    Returns:
        The dot-stuffed payload, or an empty result for empty input.
    """
    if not data:
        return b""
    stuffed, _ = dot_stuff_chunk(data, at_line_start=True)
    return stuffed


def extract_message_id(message: str) -> str:
    """Extract a server-assigned message identifier from a reply.

    The identifier is the first angle-bracketed token, otherwise the first
    token after ``queued as `` or ``id=``.

    Args:
        message: The final ``DATA`` reply text.

    Returns:
        The identifier, or an empty string when none is present.
    """
    text = message.strip()
    start = text.find("<")
    if start != -1:
        end = text.find(">", start)
        if end != -1:
            return text[start : end + 1]
    for pattern in (r"queued as\s+(\S+)", r"id=(\S+)"):
        match = re.search(pattern, text, re.IGNORECASE)
        if match is not None:
            return match.group(1)
    return ""


def _command(verb: str, address: MailboxAddress | None, params: list[str]) -> str:
    path = mailbox_to_string(address) if address is not None else ""
    command = f"{verb}:<{path}>"
    if params:
        command = f"{command} {' '.join(params)}"
    return command


def _append_body_type(params: list[str], caps: Capabilities, body_type: str) -> None:
    if body_type == _BODY_8BIT and caps.eight_bit_mime:
        params.append("BODY=8BITMIME")
    elif body_type == _BODY_BINARY and caps.binary_mime:
        params.append("BODY=BINARYMIME")


def _append_require_tls(
    params: list[str], caps: Capabilities, transaction: Transaction, *, is_tls: bool
) -> None:
    if not transaction.require_tls:
        return
    if not is_tls:
        message = "REQUIRETLS requires an active TLS session"
        raise SMTPError(550, message, _REQUIRETLS_ENHANCED)
    if not caps.require_tls:
        message = "REQUIRETLS support required"
        raise SMTPError(550, message, _REQUIRETLS_ENHANCED)
    params.append("REQUIRETLS")


def _append_delivery_by(
    params: list[str], caps: Capabilities, delivery_by: DeliveryBy | None
) -> None:
    if delivery_by is None:
        return
    if not caps.delivery_by:
        message = "smtp: DELIVERBY not supported by server"
        raise SMTPCapabilityError(message)
    value = _format_delivery_by(delivery_by)
    if delivery_by.mode.upper() == _MODE_RETURN:
        minimum = caps.delivery_by_min_seconds
        if minimum > 0 and delivery_by.seconds < minimum:
            message = (
                f"smtp: DELIVERBY BY time {delivery_by.seconds} is below server minimum {minimum}"
            )
            raise ValueError(message)
    params.append(f"BY={value}")


def _format_delivery_by(delivery_by: DeliveryBy) -> str:
    mode = delivery_by.mode.upper()
    if mode == _MODE_RETURN:
        if delivery_by.seconds <= 0:
            message = "smtp: DELIVERYBY mode R requires seconds > 0"
            raise ValueError(message)
    elif mode != _MODE_NOTIFY:
        message = f"smtp: invalid DELIVERYBY mode {delivery_by.mode!r}"
        raise ValueError(message)
    value = f"{delivery_by.seconds};{mode}"
    if delivery_by.trace:
        value = f"{value}T"
    return value


def _format_orcpt(dsn: RecipientDSN, *, smtp_utf8: bool) -> str:
    if dsn.original_recipient is not None and dsn.orcpt != "":
        message = "smtp: ORCPT specified in both orcpt and original_recipient"
        raise ValueError(message)
    if dsn.original_recipient is not None:
        address_type, address = dsn.original_recipient
        return format_original_recipient(address_type, address, smtp_utf8=smtp_utf8)
    if dsn.orcpt == "":
        return ""
    address_type, separator, address = dsn.orcpt.partition(";")
    if not separator:
        message = "smtp: invalid DSN ORCPT: expected address-type;address"
        raise ValueError(message)
    return format_original_recipient(address_type, address, smtp_utf8=smtp_utf8)
