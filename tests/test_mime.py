"""Tests for MIME body construction, ported from mxraven-js."""

from __future__ import annotations

import base64
import quopri
import re

import pytest

from mxraven._internal.mime import (
    build_alternative,
    encode_base64_lines,
    encode_body,
    encode_quoted_printable,
    exceeds_line_length,
    normalize_line_endings,
    wrap_attachments,
)
from mxraven.message import Attachment


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("a\nb", "a\r\nb"),
        ("a\r\nb", "a\r\nb"),
        ("a\rb", "a\r\nb"),
        ("a\r\nb\nc\rd", "a\r\nb\r\nc\r\nd"),
    ],
)
def test_normalize_line_endings(value: str, expected: str) -> None:
    assert normalize_line_endings(value) == expected


def test_encode_base64_lines_empty() -> None:
    assert encode_base64_lines(b"") == ""


def test_encode_base64_lines_wraps_at_76() -> None:
    data = b"abcdef" * 20
    encoded = encode_base64_lines(data)
    assert encoded.startswith(encoded[:76] + "\r\n")
    for line in encoded.rstrip("\r\n").split("\r\n"):
        assert len(line) <= 76


@pytest.mark.parametrize(
    "text",
    [
        "hello world",
        "trailing space   ",
        "tab\there\t",
        "equals = sign and ünïcode",
        "line1\r\nline2\r\n",
        "",
        "ü" * 100,
        "x" * 76,
        "ends with equals=",
    ],
)
def test_encode_quoted_printable_round_trips(text: str) -> None:
    encoded = encode_quoted_printable(text)
    assert all(ord(char) < 128 for char in encoded)
    for line in encoded.split("\r\n"):
        assert len(line) <= 76
    assert quopri.decodestring(encoded.encode("ascii")).decode("utf-8") == text


def test_encode_quoted_printable_encodes_trailing_whitespace() -> None:
    assert encode_quoted_printable("a  ") == "a=20=20"
    assert encode_quoted_printable("a\t") == "a=09"
    assert encode_quoted_printable("a b") == "a b"


def test_encode_quoted_printable_wraps_long_lines_with_soft_breaks() -> None:
    encoded = encode_quoted_printable("x" * 200)
    assert encoded.endswith("=") is False
    assert "\r\n" in encoded
    assert all(line.endswith("=") for line in encoded.split("\r\n")[:-1])
    assert quopri.decodestring(encoded.encode("ascii")) == b"x" * 200


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("z" * 998, False),
        ("z" * 999, True),
        ("ü" * 500, True),
        ("short line\r\n" * 5, False),
        ("ok\r\n" + "y" * 999, True),
    ],
)
def test_exceeds_line_length(text: str, expected: bool) -> None:
    assert exceeds_line_length(text) is expected


@pytest.mark.parametrize(
    ("text", "encoding"),
    [
        ("plain ascii", "7bit"),
        ("héllo", "8bit"),
        ("z" * 999, "quoted-printable"),
    ],
)
def test_encode_body_selects_encoding(text: str, encoding: str) -> None:
    body, selected = encode_body(text)
    assert selected == encoding
    assert body.endswith("\r\n")


def test_build_alternative() -> None:
    body, content_type, encoding = build_alternative("hello", "<p>hello</p>", boundary="B")
    assert re.match(r'^multipart/alternative; boundary=".+$', content_type)
    assert encoding == "7bit"
    assert "Content-Type: text/plain; charset=utf-8\r\n" in body
    assert "Content-Type: text/html; charset=utf-8\r\n" in body
    assert "hello" in body
    assert body.endswith("--\r\n")


def test_build_alternative_selects_8bit() -> None:
    _body, _content_type, encoding = build_alternative("héllo", "<p>hello</p>", boundary="B")
    assert encoding == "8bit"


def test_build_alternative_encodes_long_lines_per_part() -> None:
    body, _content_type, encoding = build_alternative("z" * 999, "<p>hello</p>", boundary="B")
    assert encoding == "7bit"
    assert "Content-Transfer-Encoding: quoted-printable\r\n" in body
    assert "Content-Transfer-Encoding: 7bit\r\n" in body
    assert all(len(line) <= 998 for line in body.split("\r\n"))


def test_wrap_attachments_preserves_body_encoding() -> None:
    long_body, encoding = encode_body("z" * 999)
    wrapped, _content_type, outer = wrap_attachments(
        long_body, "text/plain; charset=utf-8", encoding, [], boundary="B"
    )
    assert encoding == "quoted-printable"
    assert outer == "7bit"
    assert "Content-Transfer-Encoding: quoted-printable\r\n" in wrapped


def test_wrap_attachments() -> None:
    attachment = Attachment(
        filename="report.pdf", content_type="application/pdf", data=b"\x01\x02\x03"
    )
    body, content_type, encoding = wrap_attachments(
        "hello\r\n", "text/plain; charset=utf-8", "7bit", [attachment], boundary="B"
    )
    assert re.match(r'^multipart/mixed; boundary=".+$', content_type)
    assert encoding == "7bit"
    assert 'Content-Disposition: attachment; filename="report.pdf"\r\n' in body
    assert "Content-Transfer-Encoding: base64\r\n" in body
    assert base64.b64encode(b"\x01\x02\x03").decode() in body
    assert body.endswith("--\r\n")


def test_wrap_attachments_inline() -> None:
    attachment = Attachment(
        filename="logo.png",
        content_type="image/png",
        data=b"\x09",
        inline=True,
        content_id="logo",
    )
    body, _content_type, _encoding = wrap_attachments(
        "", "text/plain; charset=utf-8", "7bit", [attachment], boundary="B"
    )
    assert 'Content-Disposition: inline; filename="logo.png"\r\n' in body
    assert "Content-ID: <logo>\r\n" in body


def test_wrap_attachments_rejects_line_break_in_filename() -> None:
    attachment = Attachment(filename="bad\r\nname", data=b"\x01")
    with pytest.raises(ValueError, match="line break"):
        wrap_attachments("", "text/plain; charset=utf-8", "7bit", [attachment], boundary="B")
