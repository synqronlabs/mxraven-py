"""Tests for MAIL FROM / RCPT TO command construction."""

from __future__ import annotations

import pytest

from mxraven._internal.address import MailboxAddress
from mxraven._internal.smtp.extensions import capabilities_from, parse_extensions
from mxraven._internal.smtp.transaction import (
    DeliveryBy,
    Recipient,
    RecipientDSN,
    Transaction,
    build_mail_from,
    build_rcpt_to,
    dot_stuff,
    dot_stuff_chunk,
    extract_message_id,
)
from mxraven.errors import SMTPCapabilityError, SMTPError

SENDER = MailboxAddress("sender", "example.com")
RCPT = MailboxAddress("rcpt", "example.net")


def caps(*extensions: str):
    lines = ["mock.example.com Hello", *extensions]
    parsed = parse_extensions(lines)
    return capabilities_from(parsed, is_esmtp=True, hostname="mock.example.com")


def test_null_reverse_path() -> None:
    command = build_mail_from(caps(), Transaction(sender=None, recipients=()), is_tls=False)
    assert command == "MAIL FROM:<>"


def test_size_body_smtputf8() -> None:
    command = build_mail_from(
        caps("SIZE 102400", "8BITMIME", "SMTPUTF8"),
        Transaction(sender=SENDER, recipients=(), size=128, body_type="8BITMIME", smtp_utf8=True),
        is_tls=False,
    )
    assert command == "MAIL FROM:<sender@example.com> SIZE=128 BODY=8BITMIME SMTPUTF8"


def test_size_gated_on_advertisement() -> None:
    command = build_mail_from(
        caps("SIZE 102400"), Transaction(sender=SENDER, recipients=(), size=128), is_tls=False
    )
    assert command == "MAIL FROM:<sender@example.com> SIZE=128"


def test_require_tls_without_session() -> None:
    with pytest.raises(SMTPError) as info:
        build_mail_from(
            caps(), Transaction(sender=SENDER, recipients=(), require_tls=True), is_tls=False
        )
    assert info.value.code == 550
    assert info.value.enhanced_code == "5.7.30"


def test_require_tls_without_support() -> None:
    with pytest.raises(SMTPError, match="support required"):
        build_mail_from(
            caps(), Transaction(sender=SENDER, recipients=(), require_tls=True), is_tls=True
        )


def test_require_tls_supported() -> None:
    command = build_mail_from(
        caps("REQUIRETLS"), Transaction(sender=SENDER, recipients=(), require_tls=True), is_tls=True
    )
    assert command == "MAIL FROM:<sender@example.com> REQUIRETLS"


def test_delivery_by_unsupported() -> None:
    with pytest.raises(SMTPCapabilityError):
        build_mail_from(
            caps(),
            Transaction(sender=SENDER, recipients=(), delivery_by=DeliveryBy(600, "R")),
            is_tls=False,
        )


def test_delivery_by_ok() -> None:
    command = build_mail_from(
        caps("DELIVERBY 300"),
        Transaction(sender=SENDER, recipients=(), delivery_by=DeliveryBy(600, "R")),
        is_tls=False,
    )
    assert command == "MAIL FROM:<sender@example.com> BY=600;R"


def test_delivery_by_below_minimum() -> None:
    with pytest.raises(ValueError, match="below server minimum"):
        build_mail_from(
            caps("DELIVERBY 300"),
            Transaction(sender=SENDER, recipients=(), delivery_by=DeliveryBy(100, "R")),
            is_tls=False,
        )


def test_delivery_by_trace() -> None:
    command = build_mail_from(
        caps("DELIVERBY 0"),
        Transaction(sender=SENDER, recipients=(), delivery_by=DeliveryBy(0, "N", trace=True)),
        is_tls=False,
    )
    assert command == "MAIL FROM:<sender@example.com> BY=0;NT"


def test_dsn_auth_envid() -> None:
    command = build_mail_from(
        caps("DSN"),
        Transaction(
            sender=SENDER,
            recipients=(),
            auth="identity",
            dsn_ret="hdrs",
            env_id="env 1",
        ),
        is_tls=False,
    )
    assert command == "MAIL FROM:<sender@example.com> AUTH=<identity> RET=HDRS ENVID=env+201"


def test_extension_params() -> None:
    command = build_mail_from(
        caps(),
        Transaction(
            sender=SENDER,
            recipients=(),
            extension_params=(("X-TRACE", "1"), ("NOOP", "")),
        ),
        is_tls=False,
    )
    assert command == "MAIL FROM:<sender@example.com> X-TRACE=1 NOOP"


def test_rcpt_plain() -> None:
    command = build_rcpt_to(caps(), Recipient(RCPT), smtp_utf8=False)
    assert command == "RCPT TO:<rcpt@example.net>"


def test_rcpt_dsn() -> None:
    recipient = Recipient(
        RCPT,
        RecipientDSN(notify=("SUCCESS", "FAILURE"), orcpt="rfc822;orig@example.com"),
    )
    command = build_rcpt_to(caps("DSN"), recipient, smtp_utf8=False)
    assert command == (
        "RCPT TO:<rcpt@example.net> NOTIFY=SUCCESS,FAILURE ORCPT=rfc822;orig@example.com"
    )


def test_rcpt_dsn_gated_on_advertisement() -> None:
    recipient = Recipient(RCPT, RecipientDSN(notify=("SUCCESS",)))
    command = build_rcpt_to(caps(), recipient, smtp_utf8=False)
    assert command == "RCPT TO:<rcpt@example.net>"


def test_rcpt_orcpt_requires_semicolon() -> None:
    recipient = Recipient(RCPT, RecipientDSN(orcpt="no-semicolon"))
    with pytest.raises(ValueError, match="ORCPT"):
        build_rcpt_to(caps("DSN"), recipient, smtp_utf8=False)


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"", b""),
        (b"Hello\r\n", b"Hello\r\n"),
        (b".hidden\r\n", b"..hidden\r\n"),
        (b"Hello\r\n.World\r\n", b"Hello\r\n..World\r\n"),
        (b"..already\r\n", b"...already\r\n"),
        (b".line1\r\n.line2\r\n", b"..line1\r\n..line2\r\n"),
        (b".\r\n", b"..\r\n"),
        (b"body", b"body"),
        (b"body\r", b"body\r"),
        (b"body\n", b"body\n"),
        (
            b"Normal line\r\n.dot line\r\nAnother normal\r\n.another dot\r\n",
            b"Normal line\r\n..dot line\r\nAnother normal\r\n..another dot\r\n",
        ),
    ],
)
def test_dot_stuff(data: bytes, expected: bytes) -> None:
    assert dot_stuff(data) == expected


def test_dot_stuff_across_transfer_boundary() -> None:
    data = b".\n" * 16384 + b".after\r\n"
    assert dot_stuff(data) == b"..\n" * 16384 + b"..after\r\n"


def test_dot_stuff_chunk_carries_line_start_state() -> None:
    chunks = [b".first\r\n.second", b"\r\n.third\r\n."]
    at_line_start = True
    output = b""
    for chunk in chunks:
        stuffed, at_line_start = dot_stuff_chunk(chunk, at_line_start=at_line_start)
        output += stuffed
    assert output == dot_stuff(b"".join(chunks))


def test_dot_stuff_chunk_crlf_boundary() -> None:
    stuffed, at_line_start = dot_stuff_chunk(b"line\r", at_line_start=True)
    assert stuffed == b"line\r"
    assert at_line_start is False
    stuffed, at_line_start = dot_stuff_chunk(b"\n.hidden", at_line_start=at_line_start)
    assert stuffed == b"\n..hidden"
    assert at_line_start is False


def test_dot_stuff_chunk_leading_dot() -> None:
    stuffed, at_line_start = dot_stuff_chunk(b".\r\n", at_line_start=True)
    assert stuffed == b"..\r\n"
    assert at_line_start is True


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("<incomplete", ""),
        ("Ok: queued as ABC123 extra", "ABC123"),
        ("message id=XYZ789 accepted", "XYZ789"),
        ("   ", ""),
        ("queued as <ID@host>", "<ID@host>"),
        ("no identifier here", ""),
        ("Message accepted <123@server.com>", "<123@server.com>"),
        ("id=XYZ789 accepted", "XYZ789"),
        ("2.0.0 Ok: queued as TEST456", "TEST456"),
    ],
)
def test_extract_message_id(message: str, expected: str) -> None:
    assert extract_message_id(message) == expected
