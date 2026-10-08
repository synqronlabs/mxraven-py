"""Tests for the message builder, ported from the Go and TypeScript SDKs."""

from __future__ import annotations

import quopri
from datetime import UTC, datetime
from email.parser import BytesParser

import pytest

from mxraven.message import Attachment, DeliveryBy, DeliveryByMode, Envelope, Message

pytestmark = pytest.mark.filterwarnings("error")


def parse(data: bytes):
    return BytesParser().parsebytes(data)


def test_plain_text_message() -> None:
    built = (
        Message()
        .from_("Acme <noreply@acme.example>")
        .to("customer@example.com")
        .subject("Your receipt")
        .text("Thanks for your order.")
        .build()
    )
    message = parse(built.data)
    assert message["From"] == "Acme <noreply@acme.example>"
    assert message["To"] == "customer@example.com"
    assert message["Subject"] == "Your receipt"
    assert message["MIME-Version"] == "1.0"
    assert message["Content-Type"] == "text/plain; charset=utf-8"
    assert message["Content-Transfer-Encoding"] == "7bit"
    assert message["Date"] is not None
    assert message["Message-ID"] is not None
    body = built.data.split(b"\r\n\r\n", 1)[1]
    assert body == b"Thanks for your order.\r\n"
    assert built.sender == "noreply@acme.example"
    assert built.recipients == ("customer@example.com",)
    assert built.smtp_utf8 is False
    assert built.body_type == ""
    assert built.size == len(built.data)


def test_html_only_message() -> None:
    built = Message().from_("a@example.com").to("b@example.com").html("<p>Hello</p>").build()
    message = parse(built.data)
    assert message["Content-Type"] == "text/html; charset=utf-8"
    assert built.data.split(b"\r\n\r\n", 1)[1] == b"<p>Hello</p>\r\n"


def test_multipart_alternative() -> None:
    built = (
        Message()
        .from_("a@example.com")
        .to("b@example.com")
        .text("hello")
        .html("<p>hello</p>")
        .build()
    )
    message = parse(built.data)
    assert message["Content-Type"].startswith("multipart/alternative;")
    body = built.data.split(b"\r\n\r\n", 1)[1]
    assert b"Content-Type: text/plain; charset=utf-8" in body
    assert b"Content-Type: text/html; charset=utf-8" in body


def test_bcc_is_envelope_only() -> None:
    built = (
        Message()
        .from_("a@example.com")
        .to("b@example.com")
        .bcc("audit@example.com")
        .text("hi")
        .build()
    )
    message = parse(built.data)
    assert message["Bcc"] is None
    assert built.recipients == ("b@example.com", "audit@example.com")


def test_null_sender_keeps_from_header() -> None:
    built = Message().from_("Bounce <bounce@example.com>").to("b@example.com").null_sender().build()
    message = parse(built.data)
    assert message["From"] == "Bounce <bounce@example.com>"
    assert built.sender == ""


def test_non_ascii_subject_is_encoded() -> None:
    built = Message().from_("a@example.com").to("b@example.com").subject("日本語").build()
    message = parse(built.data)
    assert message["Subject"].startswith("=?UTF-8?B?")


def test_message_id_is_wrapped() -> None:
    built = (
        Message()
        .from_("a@example.com")
        .to("b@example.com")
        .message_id("custom-id@example.com")
        .build()
    )
    assert parse(built.data)["Message-ID"] == "<custom-id@example.com>"


def test_explicit_date_is_used() -> None:
    when = datetime(2024, 1, 15, 10, 30, tzinfo=UTC)
    built = Message().from_("a@example.com").to("b@example.com").date(when).build()
    assert parse(built.data)["Date"] == "Mon, 15 Jan 2024 10:30:00 +0000"


def test_attachment_is_base64() -> None:
    built = (
        Message()
        .from_("a@example.com")
        .to("b@example.com")
        .text("see attached")
        .attach_file("notes.txt", b"attached")
        .build()
    )
    message = parse(built.data)
    assert message["Content-Type"].startswith("multipart/mixed;")
    assert message["Content-Transfer-Encoding"] == "7bit"
    body = built.data.split(b"\r\n\r\n", 1)[1]
    assert b'filename="notes.txt"' in body
    assert b"Content-Transfer-Encoding: base64" in body
    assert b"YXR0YWNoZWQ=" in body


def test_inline_attachment_has_content_id() -> None:
    built = (
        Message()
        .from_("a@example.com")
        .to("b@example.com")
        .html('<img src="cid:logo">')
        .attach_inline("logo.png", "logo", b"png", content_type="image/png")
        .build()
    )
    body = built.data.split(b"\r\n\r\n", 1)[1]
    assert b'Content-Disposition: inline; filename="logo.png"' in body
    assert b"Content-ID: <logo>" in body


def test_smtp_utf8_for_international_address() -> None:
    built = Message().from_("jöhn@example.com").to("b@example.com").build()
    assert built.smtp_utf8 is True


def test_smtp_utf8_for_non_ascii_header() -> None:
    built = Message().from_("a@example.com").to("b@example.com").header("X-Note", "héllo").build()
    assert built.smtp_utf8 is True


def test_8bit_body_sets_body_type() -> None:
    built = Message().from_("a@example.com").to("b@example.com").text("héllo").build()
    assert built.body_type == "8BITMIME"


def test_long_line_body_is_quoted_printable() -> None:
    long_line = "z" * 1200
    built = Message().from_("a@example.com").to("b@example.com").text(long_line).build()
    message = parse(built.data)
    assert message["Content-Transfer-Encoding"] == "quoted-printable"
    assert built.body_type == ""
    assert all(len(line) <= 998 for line in built.data.split(b"\r\n"))
    body = built.data.split(b"\r\n\r\n", 1)[1]
    assert quopri.decodestring(body).decode("utf-8") == f"{long_line}\r\n"


def test_multipart_alternative_encodes_only_long_part() -> None:
    built = (
        Message()
        .from_("a@example.com")
        .to("b@example.com")
        .text("short")
        .html(f"<p>{'z' * 1200}</p>")
        .build()
    )
    assert parse(built.data)["Content-Transfer-Encoding"] == "7bit"
    body = built.data.split(b"\r\n\r\n", 1)[1]
    assert b"Content-Transfer-Encoding: 7bit" in body
    assert b"Content-Transfer-Encoding: quoted-printable" in body


def test_missing_from_raises() -> None:
    with pytest.raises(ValueError, match="from address is required"):
        Message().to("b@example.com").build()


def test_missing_recipient_raises() -> None:
    with pytest.raises(ValueError, match="at least one recipient"):
        Message().from_("a@example.com").build()


def test_invalid_address_raises() -> None:
    with pytest.raises(ValueError, match="mail:"):
        Message().from_("a@example.com").to("not an address").build()


def test_header_injection_raises() -> None:
    with pytest.raises(ValueError, match="header value"):
        Message().from_("a@example.com").to("b@example.com").header(
            "X-Bad", "a\r\nBcc: x@y"
        ).build()


def test_invalid_header_name_raises() -> None:
    with pytest.raises(ValueError, match="header name"):
        Message().from_("a@example.com").to("b@example.com").header("Bad Name", "x").build()


def test_delivery_by_mode_wire_values() -> None:
    assert DeliveryByMode.NOTIFY.value == "N"
    assert DeliveryByMode.RETURN.value == "R"


def test_delivery_by_defaults() -> None:
    request = DeliveryBy(600)
    assert request.seconds == 600
    assert request.mode is DeliveryByMode.NOTIFY
    assert request.trace is False


def test_envelope_delivery_options_default_off() -> None:
    envelope = Envelope()
    assert envelope.require_tls is False
    assert envelope.delivery_by is None


def test_built_message_delivery_options_default_off() -> None:
    built = Message().from_("a@example.com").to("b@example.com").build()
    assert built.require_tls is False
    assert built.delivery_by is None


def test_builder_carries_require_tls_and_deliver_by() -> None:
    request = DeliveryBy(600, DeliveryByMode.RETURN, trace=True)
    built = (
        Message()
        .from_("a@example.com")
        .to("b@example.com")
        .require_tls()
        .deliver_by(request)
        .build()
    )
    assert built.require_tls is True
    assert built.delivery_by == request


def test_attachment_defaults() -> None:
    attachment = Attachment()
    assert attachment.filename == ""
    assert attachment.content_type == ""
    assert attachment.data == b""
    assert attachment.inline is False
    assert attachment.content_id == ""


def test_cc_appears_in_headers_and_envelope() -> None:
    built = (
        Message()
        .from_("noreply@acme.example")
        .to("customer@example.com")
        .cc("ops@example.com")
        .text("Hi")
        .build()
    )
    assert parse(built.data)["Cc"] == "ops@example.com"
    assert built.recipients == ("customer@example.com", "ops@example.com")


def test_message_id_uses_sender_domain() -> None:
    built = Message().from_("noreply@acme.example").to("customer@example.com").text("Hi").build()
    message_id = parse(built.data)["Message-ID"]
    assert message_id.startswith("<")
    assert message_id.endswith("@acme.example>")


def test_attachment_only_falls_back_to_text_plain() -> None:
    built = (
        Message()
        .from_("a@example.com")
        .to("b@example.com")
        .attach_file("notes.txt", b"attached")
        .build()
    )
    body = built.data.split(b"\r\n\r\n", 1)[1]
    assert b"Content-Type: text/plain; charset=utf-8" in body
    assert b"Content-Transfer-Encoding: 7bit" in body


def test_attachment_filename_line_break_rejected() -> None:
    with pytest.raises(ValueError, match="line break"):
        Message().from_("a@example.com").to("b@example.com").attach_file(
            "bad\r\nname", b"x"
        ).build()


def test_smtp_utf8_for_non_ascii_domain() -> None:
    built = Message().from_("user@münchen.de").to("a@example.com").text("Hi").build()
    assert built.smtp_utf8 is True


def test_file_and_inline_attachment_tokens() -> None:
    built = (
        Message()
        .from_("a@example.com")
        .to("b@example.com")
        .text("hello")
        .attach_file("notes.txt", b"attached")
        .attach_inline("logo.png", "logo", b"png")
        .build()
    )
    body = built.data.split(b"\r\n\r\n", 1)[1]
    for token in (b"hello", b"notes.txt", b"YXR0YWNoZWQ=", b"logo.png", b"inline", b"cG5n"):
        assert token in body
