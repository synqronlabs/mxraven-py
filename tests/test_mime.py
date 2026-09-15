"""Tests for MIME body construction, ported from mxraven-js."""

from __future__ import annotations

import base64
import re

import pytest

from mxraven._internal.mime import (
    build_alternative,
    encode_base64_lines,
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
