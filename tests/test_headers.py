"""Tests for header folding and validation, ported from mxraven-js."""

from __future__ import annotations

import pytest

from mxraven._internal.headers import (
    fold_header,
    serialize_headers,
    validate_header,
    validate_header_name,
)


def split_lines(value: str) -> list[str]:
    return [line for line in value.split("\r\n") if line != ""]


def test_short_header_is_not_folded() -> None:
    assert serialize_headers([("Subject", "Hello")]) == b"Subject: Hello\r\n"


def test_header_at_recommended_limit_is_not_folded() -> None:
    value = "This is exactly at the seventy-eight character limit, yes it is!!!!!!"
    assert len(value) == 69
    assert serialize_headers([("Subject", value)]) == f"Subject: {value}\r\n".encode()


def test_long_header_folds_at_whitespace() -> None:
    value = "This is a longer subject line that will definitely need to be folded at whitespace"
    lines = split_lines(serialize_headers([("Subject", value)]).decode())
    assert len(lines) >= 2
    for line in lines:
        assert len(line) <= 78
    for line in lines[1:]:
        assert line.startswith(" ") or line.startswith("\t")


def test_consecutive_whitespace_is_collapsed_at_fold_points() -> None:
    value = (
        "word1 word2  word3   word4    word5 word6 word7 word8 word9 word10 word11 word12 word13"
    )
    lines = split_lines(serialize_headers([("Subject", value)]).decode())
    for line in lines[1:]:
        assert not line.startswith("  ")


def test_folds_on_tabs() -> None:
    value = "word1\tword2\tword3 word4 word5 word6 word7 word8 word9 word10 word11 word12"
    lines = split_lines(serialize_headers([("Subject", value)]).decode())
    assert len(lines) >= 2
    for line in lines:
        assert len(line) <= 78


def test_unbreakable_value_never_exceeds_hard_limit() -> None:
    value = (
        "ThisIsAVeryLongWordWithNoWhitespaceAtAllAndItJustKeepsGoingAndGoingAndGoing"
        "UntilItExceedsTheMaximumLineLength"
    )
    for line in split_lines(serialize_headers([("X-Long", value)]).decode()):
        assert len(line) <= 998


def test_empty_value() -> None:
    assert serialize_headers([("X-Empty", "")]) == b"X-Empty: \r\n"


def test_whitespace_only_value_ends_with_crlf() -> None:
    assert serialize_headers([("X-Spaces", "   ")]).endswith(b"\r\n")


def test_unfolding_preserves_content() -> None:
    value = (
        "This is a test value with multiple words that should be folded and then unfolded correctly"
    )
    serialized = serialize_headers([("Subject", value)]).decode()
    unfolded = serialized[len("Subject: ") : -2]
    for separator in ("\r\n ", "\r\n\t"):
        unfolded = unfolded.replace(separator, " ")
    while "  " in unfolded:
        unfolded = unfolded.replace("  ", " ")
    collapsed = " ".join(value.split())
    assert unfolded == collapsed


def test_rejects_crlf_injection() -> None:
    with pytest.raises(ValueError, match="line break"):
        validate_header("Subject", "ok\r\nX-Injected: bad")


def test_rejects_invalid_header_name() -> None:
    with pytest.raises(ValueError, match="invalid header name"):
        validate_header_name("Bad Header")


def test_accepts_normal_header() -> None:
    assert fold_header("X-Campaign", "launch") == "X-Campaign: launch\r\n"
