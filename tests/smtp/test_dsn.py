"""Tests for RFC 3461 DSN parameter encoding, ported from mxraven-js."""

from __future__ import annotations

import pytest

from mxraven._internal.smtp.dsn import (
    encode_xtext,
    format_envelope_id,
    format_original_recipient,
    normalize_notify,
    normalize_return,
    parse_xtext,
)


def test_parse_xtext_round_trip() -> None:
    assert parse_xtext("one+20two+2Bthree+3Dfour") == "one two+three=four"


@pytest.mark.parametrize("wire", ["+", "+2", "+2f", "+GG", "raw=value", "raw value"])
def test_parse_xtext_rejects(wire: str) -> None:
    with pytest.raises(ValueError, match="dsn:"):
        parse_xtext(wire)


def test_encode_xtext() -> None:
    assert encode_xtext("one two+three=four") == "one+20two+2Bthree+3Dfour"


def test_xtext_round_trip() -> None:
    value = "a b+c=d"
    assert parse_xtext(encode_xtext(value)) == value


def test_normalize_return() -> None:
    assert normalize_return("full") == "FULL"
    assert normalize_return("HDRS") == "HDRS"
    with pytest.raises(ValueError, match="RET"):
        normalize_return("PARTIAL")
    with pytest.raises(ValueError, match="line break"):
        normalize_return("FULL\r\nX: y")


def test_normalize_notify() -> None:
    assert normalize_notify(["success", "failure"]) == ["SUCCESS", "FAILURE"]
    assert normalize_notify(["never"]) == ["NEVER"]
    with pytest.raises(ValueError, match="alone"):
        normalize_notify(["NEVER", "SUCCESS"])
    with pytest.raises(ValueError, match="empty"):
        normalize_notify([])
    with pytest.raises(ValueError, match="invalid NOTIFY"):
        normalize_notify(["MAYBE"])


def test_format_envelope_id() -> None:
    assert format_envelope_id("env-1") == "env-1"
    with pytest.raises(ValueError, match="ENVID"):
        format_envelope_id("")
    with pytest.raises(ValueError, match="ENVID"):
        format_envelope_id("a\nb")
    with pytest.raises(ValueError, match="exceeds"):
        format_envelope_id("x" * 200)


def test_format_original_recipient() -> None:
    assert (
        format_original_recipient("rfc822", "orig@example.com", smtp_utf8=False)
        == "rfc822;orig@example.com"
    )
    assert (
        format_original_recipient("utf-8", "ü@example.com", smtp_utf8=False)
        == "utf-8;\\x{FC}@example.com"
    )
    assert (
        format_original_recipient("utf-8", "ü@example.com", smtp_utf8=True) == "utf-8;ü@example.com"
    )
    with pytest.raises(ValueError, match="address type"):
        format_original_recipient("bad type", "a@example.com", smtp_utf8=False)
    with pytest.raises(ValueError, match="ORCPT"):
        format_original_recipient("rfc822", "a\r\nb", smtp_utf8=False)
