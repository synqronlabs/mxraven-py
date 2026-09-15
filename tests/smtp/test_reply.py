"""Tests for SMTP reply parsing, ported from raven and mxraven-js."""

from __future__ import annotations

import pytest

from mxraven._internal.smtp.reply import (
    Reply,
    parse_enhanced_code,
    parse_reply_line,
    reply_from_lines,
)
from mxraven.errors import SMTPProtocolError


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("2.0.0 Ok", "2.0.0"),
        ("5.1.1 User unknown", "5.1.1"),
        ("4.7.0 Try again later", "4.7.0"),
        ("Ok", ""),
        ("", ""),
        ("abc", ""),
        ("2.0 short", ""),
        ("2.0.0", "2.0.0"),
        ("notanum.0.0 test", ""),
        ("2.notanum.0 test", ""),
        ("2.0.notanum test", ""),
        ("12.34.56 large numbers", "12.34.56"),
        ("2.-1.0 signed", ""),
        ("2.0.0  extra spacing", "2.0.0"),
        (" 2.0.0 leading space", ""),
    ],
)
def test_parse_enhanced_code(message: str, expected: str) -> None:
    assert parse_enhanced_code(message) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("250 OK", (250, "OK", True)),
        ("250-OK", (250, "OK", False)),
        ("250 ", (250, "", True)),
        ("250", None),
        ("25", None),
        ("", None),
        ("2x0 OK", None),
        ("250-", (250, "", False)),
        ("354 Send data", (354, "Send data", True)),
    ],
)
def test_parse_reply_line(raw: str, expected: tuple[int, str, bool] | None) -> None:
    if expected is None:
        with pytest.raises(SMTPProtocolError):
            parse_reply_line(raw)
    else:
        assert parse_reply_line(raw) == expected


def test_reply_from_lines_multiline() -> None:
    reply = reply_from_lines(["250-mock.example.com Hello", "250-SIZE 10485760", "250 PIPELINING"])
    assert reply.code == 250
    assert reply.lines == (
        "mock.example.com Hello",
        "SIZE 10485760",
        "PIPELINING",
    )
    assert reply.message == "mock.example.com Hello\nSIZE 10485760\nPIPELINING"
    assert reply.enhanced_code == ""


def test_reply_from_lines_enhanced_code_comes_from_first_line() -> None:
    reply = reply_from_lines(["550-5.1.1 User unknown", "550 Try another"])
    assert reply.code == 550
    assert reply.enhanced_code == "5.1.1"


def test_reply_from_lines_rejects_inconsistent_codes() -> None:
    with pytest.raises(SMTPProtocolError):
        reply_from_lines(["250 OK", "550 No"])


def test_reply_from_lines_rejects_empty() -> None:
    with pytest.raises(SMTPProtocolError):
        reply_from_lines([])


@pytest.mark.parametrize(
    ("code", "success", "intermediate", "transient", "permanent"),
    [
        (0, False, False, False, False),
        (100, False, False, False, False),
        (199, False, False, False, False),
        (200, True, False, False, False),
        (250, True, False, False, False),
        (299, True, False, False, False),
        (300, False, True, False, False),
        (354, False, True, False, False),
        (399, False, True, False, False),
        (400, False, False, True, False),
        (421, False, False, True, False),
        (499, False, False, True, False),
        (500, False, False, False, True),
        (550, False, False, False, True),
        (599, False, False, False, True),
        (600, False, False, False, False),
    ],
)
def test_reply_classification(
    code: int,
    success: bool,
    intermediate: bool,
    transient: bool,
    permanent: bool,
) -> None:
    reply = Reply(code=code, message="m", lines=("m",))
    assert reply.is_success is success
    assert reply.is_intermediate is intermediate
    assert reply.is_transient_error is transient
    assert reply.is_permanent_error is permanent


def test_reply_to_error() -> None:
    assert Reply(250, "OK", ("OK",)).to_error() is None
    assert Reply(354, "Go", ("Go",)).to_error() is None
    error = Reply(550, "rejected", ("rejected",), enhanced_code="5.1.1").to_error()
    assert error is not None
    assert error.code == 550
    assert error.enhanced_code == "5.1.1"
    assert str(error) == "smtp 550 5.1.1: rejected"
