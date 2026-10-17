"""MIME parsing tests, ported from mxraven-go and mxraven-js."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from mxraven.errors import MimeNestingError, MimePartsError
from mxraven.mime import parse_email

ALTERNATIVE_WITH_ATTACHMENT = (
    b"Subject: Test message\n"
    b'From: "Alice Example" <alice@example.com>\n'
    b'To: bob@example.com, "Carol" <carol@example.com>\n'
    b"Date: Sun, 13 Sep 2026 14:30:00 +0000\n"
    b"Message-ID: <abc@example.com>\n"
    b"MIME-Version: 1.0\n"
    b'Content-Type: multipart/mixed; boundary="BOUND1"\n'
    b"\n"
    b"--BOUND1\n"
    b'Content-Type: multipart/alternative; boundary="BOUND2"\n'
    b"\n"
    b"--BOUND2\n"
    b"Content-Type: text/plain; charset=utf-8\n"
    b"Content-Transfer-Encoding: quoted-printable\n"
    b"\n"
    b"Hello =E2=9C=93\n"
    b"soft=\n"
    b"break\n"
    b"--BOUND2\n"
    b"Content-Type: text/html; charset=utf-8\n"
    b"Content-Transfer-Encoding: 7bit\n"
    b"\n"
    b"<html><body>Hi</body></html>\n"
    b"--BOUND2--\n"
    b"--BOUND1\n"
    b'Content-Type: application/octet-stream; name="hello.txt"\n'
    b'Content-Disposition: attachment; filename="hello.txt"\n'
    b"Content-Transfer-Encoding: base64\n"
    b"\n"
    b"SGVsbG8gZmlsZSE=\n"
    b"--BOUND1--"
)


def test_parses_headers_addresses_and_date() -> None:
    email = parse_email(ALTERNATIVE_WITH_ATTACHMENT)
    assert email.subject == "Test message"
    assert email.message_id == "abc@example.com"
    assert email.date == datetime(2026, 9, 13, 14, 30, tzinfo=UTC)
    assert len(email.from_) == 1
    assert email.from_[0].address == "alice@example.com"
    assert email.from_[0].name == "Alice Example"
    assert [address.address for address in email.to] == ["bob@example.com", "carol@example.com"]
    assert email.to[0].name == ""
    assert email.to[1].name == "Carol"


def test_extracts_text_and_html_bodies() -> None:
    email = parse_email(ALTERNATIVE_WITH_ATTACHMENT)
    assert email.text == "Hello \u2713\nsoftbreak"
    assert email.html == "<html><body>Hi</body></html>"


def test_extracts_decoded_attachments() -> None:
    email = parse_email(ALTERNATIVE_WITH_ATTACHMENT)
    assert len(email.attachments) == 1
    attachment = email.attachments[0]
    assert attachment.filename == "hello.txt"
    assert attachment.content_type == "application/octet-stream"
    assert attachment.inline is False
    assert attachment.content == b"Hello file!"


def test_decodes_rfc2047_encoded_words() -> None:
    raw = (
        b"Subject: =?UTF-8?B?SGVsbG8gV29ybGQ=?=\r\n"
        b"From: =?UTF-8?Q?Jos=C3=A9?= <jose@example.com>\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"body"
    )
    email = parse_email(raw)
    assert email.subject == "Hello World"
    assert email.from_[0].address == "jose@example.com"
    assert email.from_[0].name == "Jos\u00e9"


def test_merges_adjacent_encoded_words() -> None:
    raw = b"Subject: =?utf-8?B?SGVsbG8=?= =?utf-8?B?V29ybGQ=?=\r\n\r\nbody"
    assert parse_email(raw).subject == "HelloWorld"


def test_decodes_raw_utf8_header_bytes() -> None:
    raw = b"Subject: h\xc3\xa9llo\r\n\r\nbody"
    assert parse_email(raw).subject == "h\u00e9llo"


def test_decodes_simple_body() -> None:
    email = parse_email(b"Subject: Simple\r\nContent-Type: text/plain\r\n\r\njust text")
    assert email.text == "just text"
    assert email.attachments == ()


def test_decodes_rfc2231_filename() -> None:
    raw = (
        b"Content-Type: application/octet-stream\r\n"
        b"Content-Disposition: attachment; filename*=UTF-8''%C3%A9.txt\r\n"
        b"\r\n"
        b"data"
    )
    assert parse_email(raw).attachments[0].filename == "\u00e9.txt"


def test_reassembles_rfc2231_continuations() -> None:
    raw = (
        b"Content-Type: application/octet-stream\r\n"
        b"Content-Disposition: attachment; filename*0*=UTF-8''%C3%A9; filename*1*=.txt\r\n"
        b"\r\n"
        b"data"
    )
    assert parse_email(raw).attachments[0].filename == "\u00e9.txt"


def test_handles_empty_message() -> None:
    email = parse_email(b"")
    assert email.subject == ""
    assert email.headers == ()
    assert email.text == ""
    assert email.attachments == ()


def test_handles_lf_only_line_endings() -> None:
    email = parse_email(b"Subject: LF\n\nbody text")
    assert email.subject == "LF"
    assert email.text == "body text"


def test_decodes_base64_with_embedded_whitespace() -> None:
    raw = (
        b"Content-Type: application/octet-stream\r\n"
        b'Content-Disposition: attachment; filename="a.bin"\r\n'
        b"Content-Transfer-Encoding: base64\r\n"
        b"\r\n"
        b"SGVs\r\nbG8g\r\nZmlsZSE="
    )
    assert parse_email(raw).attachments[0].content == b"Hello file!"


def test_exposes_inline_parts_from_multipart_related() -> None:
    raw = (
        b'Content-Type: multipart/related; boundary="R"\r\n'
        b"\r\n"
        b"--R\r\n"
        b"Content-Type: text/html\r\n"
        b"\r\n"
        b'<img src="cid:img1">\r\n'
        b"--R\r\n"
        b"Content-Type: image/png\r\n"
        b"Content-ID: <img1>\r\n"
        b'Content-Disposition: inline; filename="logo.png"\r\n'
        b"Content-Transfer-Encoding: base64\r\n"
        b"\r\n"
        b"AQID\r\n"
        b"--R--"
    )
    email = parse_email(raw)
    assert email.html == '<img src="cid:img1">'
    assert len(email.attachments) == 1
    attachment = email.attachments[0]
    assert attachment.filename == "logo.png"
    assert attachment.content_type == "image/png"
    assert attachment.content_id == "img1"
    assert attachment.inline is True
    assert attachment.content == b"\x01\x02\x03"


def test_keeps_quoted_commas_in_display_names() -> None:
    raw = (
        b'To: "Doe, John" <john@example.com>, jane@example.com\r\n'
        b"Content-Type: text/plain\r\n"
        b"\r\n"
        b"body"
    )
    email = parse_email(raw)
    assert [address.address for address in email.to] == ["john@example.com", "jane@example.com"]
    assert email.to[0].name == "Doe, John"


def test_surfaces_message_rfc822_as_attachment() -> None:
    raw = (
        b"Content-Type: message/rfc822\r\n"
        b"\r\n"
        b"From: inner@example.com\r\n"
        b"Subject: Inner\r\n"
        b"\r\n"
        b"inner body"
    )
    email = parse_email(raw)
    assert len(email.attachments) == 1
    assert email.attachments[0].content_type == "message/rfc822"
    assert b"inner body" in email.attachments[0].content


def test_unfolds_header_continuations() -> None:
    assert parse_email(b"Subject: Hello\r\n World\r\n\r\nbody").subject == "Hello World"


def test_decodes_declared_non_utf8_charset() -> None:
    raw = b"Content-Type: text/plain; charset=windows-1252\r\n\r\n\x93"
    assert parse_email(raw).text == "\u201c"


def test_falls_back_to_utf8_for_unknown_charset() -> None:
    raw = "Content-Type: text/plain; charset=x-unknown\r\n\r\nh\u00e9llo".encode()
    assert parse_email(raw).text == "h\u00e9llo"


def test_falls_back_to_latin1_for_invalid_utf8() -> None:
    raw = b"Content-Type: text/plain; charset=us-ascii\r\n\r\n\xe9"
    assert parse_email(raw).text == "\u00e9"


def test_malformed_address_falls_back() -> None:
    raw = b'To: not an address, "Doe, John" <john@example.com>\r\n\r\nbody'
    email = parse_email(raw)
    assert len(email.to) == 2
    assert email.to[1].address == "john@example.com"


def test_content_id_makes_inline() -> None:
    raw = b"Content-Type: image/png\r\nContent-ID: <img-1>\r\n\r\npng"
    email = parse_email(raw)
    assert len(email.attachments) == 1
    assert email.attachments[0].inline is True
    assert email.attachments[0].content_id == "img-1"


def test_attachment_disposition_is_not_inline() -> None:
    raw = (
        b"Content-Type: image/png\r\n"
        b"Content-ID: <img-1>\r\n"
        b'Content-Disposition: attachment; filename="logo.png"\r\n'
        b"\r\n"
        b"png"
    )
    email = parse_email(raw)
    assert email.attachments[0].inline is False


def test_rejects_deep_nesting() -> None:
    message = "Content-Type: text/plain\r\n\r\nleaf"
    for depth in range(25):
        boundary = f"D{depth}"
        message = (
            f'Content-Type: multipart/mixed; boundary="{boundary}"\r\n\r\n'
            f"--{boundary}\r\n{message}\r\n--{boundary}--"
        )
    with pytest.raises(MimeNestingError):
        parse_email(message.encode())


def test_rejects_too_many_parts() -> None:
    parts = "".join("--B\r\nContent-Type: text/plain\r\n\r\nx\r\n" for _ in range(2001))
    message = f"Content-Type: multipart/mixed; boundary=B\r\n\r\n{parts}--B--\r\n"
    with pytest.raises(MimePartsError):
        parse_email(message.encode())


def test_attachment_content_does_not_alias_input() -> None:
    raw = (
        "Content-Type: application/octet-stream\r\n"
        'Content-Disposition: attachment; filename="a.bin"\r\n'
        "\r\n"
        "payload"
    )
    source = bytearray(raw.encode())
    email = parse_email(source)
    source[:] = b"\x00" * len(source)
    assert email.attachments[0].content == b"payload"
