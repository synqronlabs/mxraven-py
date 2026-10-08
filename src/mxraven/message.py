"""Message composition.

:class:`Message` is a mutable, chainable builder. A message may be sent more
than once; each :meth:`Message.build` serializes the current state. A
:class:`Message` is not safe for concurrent use, but the
:class:`BuiltMessage` it produces is immutable and may be shared.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import format_datetime, make_msgid
from enum import StrEnum
from typing import Self

from mxraven._internal.address import (
    MailboxAddress,
    contains_non_ascii,
    encode_rfc2047,
    format_address,
    format_address_list,
    mailbox_to_string,
    parse_address,
)
from mxraven._internal.headers import serialize_headers
from mxraven._internal.mime import (
    build_alternative,
    encode_body,
    new_boundary,
    wrap_attachments,
)

__all__ = [
    "Attachment",
    "BuiltMessage",
    "DeliveryBy",
    "DeliveryByMode",
    "Envelope",
    "Header",
    "Message",
]

_ASCII_LIMIT = 0x7F


@dataclass(frozen=True, slots=True)
class Header:
    """A custom message header.

    Attributes:
        name: The header field name.
        value: The header field value. It must not contain a line break.
    """

    name: str
    value: str


@dataclass(frozen=True, slots=True)
class Attachment:
    """A message attachment.

    Attributes:
        filename: The attachment filename. It may be empty.
        content_type: The media type; defaults to ``application/octet-stream``
            when empty.
        data: The raw attachment content. The caller retains ownership; it is
            not mutated.
        inline: Whether the attachment is meant for inline display, for example
            an image referenced by `content_id` from an HTML body.
        content_id: The inline content identifier, ignored unless `inline` is
            true.
    """

    filename: str = ""
    content_type: str = ""
    data: bytes = b""
    inline: bool = False
    content_id: str = ""


class DeliveryByMode(StrEnum):
    """How a server handles a message that misses its ``DELIVERBY`` deadline.

    The values are the RFC 2852 wire values.
    """

    NOTIFY = "N"
    """Request a delayed delivery notification (wire value ``N``)."""

    RETURN = "R"
    """Request that the message be returned to the sender (wire value ``R``)."""


@dataclass(frozen=True, slots=True)
class DeliveryBy:
    """An RFC 2852 ``DELIVERBY`` request.

    Attributes:
        seconds: The delivery interval in seconds. It must be positive when
            `mode` is `DeliveryByMode.RETURN`; ``0`` is allowed with
            `DeliveryByMode.NOTIFY`.
        mode: How the server handles a message that misses the deadline.
        trace: Whether to request a trace of the delivery.
    """

    seconds: int
    mode: DeliveryByMode = DeliveryByMode.NOTIFY
    trace: bool = False


@dataclass(frozen=True, slots=True)
class Envelope:
    """The SMTP envelope for a raw message.

    Attributes:
        sender: The envelope sender. An empty value requests a null
            reverse-path, which is appropriate for bounce messages.
        recipients: At least one envelope recipient.
        require_tls: Whether to request that TLS be used on every delivery hop
            (RFC 8689). The session must already be encrypted and the service
            must advertise ``REQUIRETLS``.
        delivery_by: An RFC 2852 ``DELIVERBY`` request, when the service
            advertises ``DELIVERBY``.
    """

    sender: str = ""
    recipients: tuple[str, ...] = ()
    require_tls: bool = False
    delivery_by: DeliveryBy | None = None


@dataclass(frozen=True, slots=True)
class BuiltMessage:
    """A serialized message and its submission envelope.

    Attributes:
        data: The serialized RFC 5322 message bytes.
        sender: The envelope sender, or an empty string for a null reverse-path.
        recipients: The envelope recipients, in header order with ``Bcc`` last.
        smtp_utf8: Whether the message needs the ``SMTPUTF8`` extension.
        size: The serialized message size in bytes.
        body_type: ``8BITMIME`` when the body contains non-ASCII bytes,
            otherwise an empty string.
        require_tls: Whether the message requests ``REQUIRETLS`` (RFC 8689).
        delivery_by: The message's ``DELIVERBY`` request (RFC 2852), if any.
    """

    data: bytes
    sender: str
    recipients: tuple[str, ...]
    smtp_utf8: bool
    size: int
    body_type: str = ""
    require_tls: bool = False
    delivery_by: DeliveryBy | None = None


@dataclass(frozen=True, slots=True)
class _Addresses:
    parsed_sender: MailboxAddress | None
    envelope_sender: str
    header_from: str
    to: list[MailboxAddress]
    cc: list[MailboxAddress]
    bcc: list[MailboxAddress]

    @property
    def all(self) -> list[MailboxAddress]:
        return [*self.to, *self.cc, *self.bcc]


class Message:
    """A mutable, chainable builder for an email message.

    Chained methods return the receiver. Set both :meth:`text` and :meth:`html`
    to produce a ``multipart/alternative`` body; attachments produce a
    ``multipart/mixed`` body. ``Bcc`` recipients are added to the envelope but
    not to the headers.

    Example:
        ```python
        message = (
            Message()
            .from_("Acme <noreply@acme.example>")
            .to("customer@example.com")
            .subject("Your receipt")
            .text("Thanks for your order.")
        )
        ```
    """

    def __init__(self) -> None:
        """Create an empty message builder."""
        self._from = ""
        self._null_sender = False
        self._sender = ""
        self._reply_to = ""
        self._to: list[str] = []
        self._cc: list[str] = []
        self._bcc: list[str] = []
        self._subject = ""
        self._text = ""
        self._html = ""
        self._headers: list[Header] = []
        self._attachments: list[Attachment] = []
        self._message_id = ""
        self._in_reply_to = ""
        self._references: list[str] = []
        self._date: datetime | None = None
        self._require_tls = False
        self._delivery_by: DeliveryBy | None = None

    def from_(self, address: str) -> Self:
        """Set the envelope sender and the ``From`` header.

        The name is `from_` because `from` is a Python keyword. The address may
        include a display name, for example ``Acme <noreply@acme.example>``.
        """
        self._from = address
        return self

    def null_sender(self) -> Self:
        """Use a null reverse-path while keeping the ``From`` header.

        Intended for bounce and other auto-generated messages. A ``From`` header
        is still required for a valid message.
        """
        self._null_sender = True
        return self

    def require_tls(self) -> Self:
        """Require TLS for every hop that handles the message (RFC 8689).

        The session must already be encrypted and the submission service must
        advertise ``REQUIRETLS``; otherwise sending raises
        :class:`mxraven.SMTPError` before the message is transmitted.
        """
        self._require_tls = True
        return self

    def deliver_by(self, request: DeliveryBy) -> Self:
        """Request delivery within a bounded window (RFC 2852).

        The submission service must advertise ``DELIVERBY``, otherwise sending
        raises :class:`mxraven.SMTPCapabilityError`.
        """
        self._delivery_by = request
        return self

    def sender(self, address: str) -> Self:
        """Set the ``Sender`` header, required when ``From`` has several mailboxes."""
        self._sender = address
        return self

    def reply_to(self, address: str) -> Self:
        """Set the ``Reply-To`` header."""
        self._reply_to = address
        return self

    def to(self, *addresses: str) -> Self:
        """Add envelope and ``To`` header recipients."""
        self._to.extend(addresses)
        return self

    def cc(self, *addresses: str) -> Self:
        """Add envelope and ``Cc`` header recipients."""
        self._cc.extend(addresses)
        return self

    def bcc(self, *addresses: str) -> Self:
        """Add envelope recipients without a visible ``Bcc`` header."""
        self._bcc.extend(addresses)
        return self

    def subject(self, subject: str) -> Self:
        """Set the ``Subject`` header. Non-ASCII subjects are encoded."""
        self._subject = subject
        return self

    def message_id(self, message_id: str) -> Self:
        """Set the ``Message-ID`` header, wrapping the value in angle brackets."""
        self._message_id = message_id
        return self

    def in_reply_to(self, message_id: str) -> Self:
        """Set the ``In-Reply-To`` header for threading."""
        self._in_reply_to = message_id
        return self

    def references(self, *message_ids: str) -> Self:
        """Set the ``References`` header for threading."""
        self._references.extend(message_ids)
        return self

    def date(self, when: datetime) -> Self:
        """Set the ``Date`` header. When unset, the time of building is used.

        A naive `when` is interpreted as UTC.
        """
        self._date = when
        return self

    def text(self, body: str) -> Self:
        """Set the plain-text body."""
        self._text = body
        return self

    def html(self, body: str) -> Self:
        """Set the HTML body."""
        self._html = body
        return self

    def header(self, name: str, value: str) -> Self:
        """Append a custom header."""
        self._headers.append(Header(name, value))
        return self

    def attach(self, attachment: Attachment) -> Self:
        """Append an attachment.

        Note:
            The attachment data is retained by reference until the message is
            sent. Callers must not mutate it in the meantime.
        """
        self._attachments.append(attachment)
        return self

    def attach_file(self, filename: str, data: bytes, *, content_type: str = "") -> Self:
        """Append a file attachment with an optional explicit content type."""
        return self.attach(Attachment(filename=filename, data=data, content_type=content_type))

    def attach_inline(
        self, filename: str, content_id: str, data: bytes, *, content_type: str = ""
    ) -> Self:
        """Append an inline attachment referenced by `content_id` from HTML."""
        return self.attach(
            Attachment(
                filename=filename,
                data=data,
                content_type=content_type,
                inline=True,
                content_id=content_id,
            )
        )

    def build(self) -> BuiltMessage:
        """Serialize the message into bytes and its submission envelope.

        Returns:
            The serialized message and envelope.

        Raises:
            ValueError: The message has no ``From`` address or header, has no
                recipients, an address is invalid, or a header is invalid.
        """
        addresses = self._resolve_addresses()
        headers = self._assemble_headers(addresses)
        body_text, content_type, encoding = self._render_body()
        headers.append(("MIME-Version", "1.0"))
        headers.append(("Content-Type", content_type))
        headers.append(("Content-Transfer-Encoding", encoding))

        body = body_text.encode("utf-8")
        data = serialize_headers(headers) + b"\r\n" + body
        body_type = "8BITMIME" if any(byte > _ASCII_LIMIT for byte in body) else ""
        return BuiltMessage(
            data=data,
            sender=addresses.envelope_sender,
            recipients=tuple(mailbox_to_string(address) for address in addresses.all),
            smtp_utf8=_needs_smtp_utf8(headers, addresses),
            size=len(data),
            body_type=body_type,
            require_tls=self._require_tls,
            delivery_by=self._delivery_by,
        )

    def _resolve_addresses(self) -> _Addresses:
        parsed_sender = None
        envelope_sender = ""
        header_from = ""
        if self._from:
            parsed_sender = parse_address(self._from)
            header_from = format_address(parsed_sender)
            envelope_sender = mailbox_to_string(parsed_sender)
        if self._null_sender:
            envelope_sender = ""

        has_from_header = bool(header_from) or any(
            header.name.lower() == "from" for header in self._headers
        )
        if not has_from_header:
            message = "mail: a from address is required"
            raise ValueError(message)

        addresses = _Addresses(
            parsed_sender=parsed_sender,
            envelope_sender=envelope_sender,
            header_from=header_from,
            to=[parse_address(address) for address in self._to],
            cc=[parse_address(address) for address in self._cc],
            bcc=[parse_address(address) for address in self._bcc],
        )
        if not addresses.all:
            message = "mail: at least one recipient is required"
            raise ValueError(message)
        return addresses

    def _assemble_headers(self, addresses: _Addresses) -> list[tuple[str, str]]:
        headers: list[tuple[str, str]] = []
        if addresses.header_from:
            headers.append(("From", addresses.header_from))
        if self._sender:
            headers.append(("Sender", format_address(parse_address(self._sender))))
        if self._reply_to:
            headers.append(("Reply-To", format_address(parse_address(self._reply_to))))
        if addresses.to:
            headers.append(("To", format_address_list(addresses.to)))
        if addresses.cc:
            headers.append(("Cc", format_address_list(addresses.cc)))
        if self._subject:
            subject = self._subject
            if contains_non_ascii(subject):
                subject = encode_rfc2047(subject)
            headers.append(("Subject", subject))
        if self._message_id:
            headers.append(("Message-ID", _wrap_angle(self._message_id)))
        if self._in_reply_to:
            headers.append(("In-Reply-To", _wrap_angle(self._in_reply_to)))
        if self._references:
            headers.append(("References", " ".join(_wrap_angle(ref) for ref in self._references)))
        headers.extend((header.name, header.value) for header in self._headers)
        self._add_date_and_message_id(headers, addresses)
        return headers

    def _add_date_and_message_id(
        self, headers: list[tuple[str, str]], addresses: _Addresses
    ) -> None:
        names = {name.lower() for name, _ in headers}
        when = self._date if self._date is not None else datetime.now(UTC)
        if "date" not in names:
            headers.append(("Date", _format_date(when)))
        if "message-id" not in names:
            headers.append(("Message-ID", make_msgid(domain=_message_id_domain(addresses))))

    def _render_body(self) -> tuple[str, str, str]:
        has_text = self._text != ""
        has_html = self._html != ""
        if has_text and has_html:
            body, content_type, encoding = build_alternative(
                self._text, self._html, boundary=new_boundary()
            )
        elif has_html:
            body, encoding = encode_body(self._html)
            content_type = "text/html; charset=utf-8"
        elif has_text:
            body, encoding = encode_body(self._text)
            content_type = "text/plain; charset=utf-8"
        else:
            body = ""
            content_type = "text/plain; charset=utf-8"
            encoding = "7bit"
        if self._attachments:
            body, content_type, encoding = wrap_attachments(
                body, content_type, encoding, self._attachments, boundary=new_boundary()
            )
        return body, content_type, encoding


def _message_id_domain(addresses: _Addresses) -> str:
    sender = addresses.parsed_sender
    if sender is not None and sender.domain:
        return sender.domain
    if addresses.all and addresses.all[0].domain:
        return addresses.all[0].domain
    return "localhost"


def _needs_smtp_utf8(headers: list[tuple[str, str]], addresses: _Addresses) -> bool:
    if any(contains_non_ascii(value) for _, value in headers):
        return True
    candidates = [addresses.parsed_sender, *addresses.all]
    return any(
        address is not None
        and (contains_non_ascii(address.local_part) or contains_non_ascii(address.domain))
        for address in candidates
    )


def _wrap_angle(value: str) -> str:
    if value.startswith("<"):
        return value
    return f"<{value}>"


def _format_date(when: datetime) -> str:
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return format_datetime(when)
