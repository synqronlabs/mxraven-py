"""Tests for submission results and message-reference parsing."""

from __future__ import annotations

import pytest

from mxraven.errors import SMTPError
from mxraven.result import RecipientResult, Result, parse_message_ref


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "2.0.0 accepted; message_ref=86a33087-51ab-40a2-a020-d2745fe08d34",
            "86a33087-51ab-40a2-a020-d2745fe08d34",
        ),
        ("2.0.0 accepted; message_ref=<abc-123>", "abc-123"),
        ("2.0.0 accepted; message_ref=abc; extra", "abc"),
        ("2.0.0 accepted; message_ref=<abc-123>\nmore", "abc-123"),
        ("2.0.0 accepted; message_ref=", ""),
        ("2.0.0 accepted", ""),
        ("", ""),
    ],
)
def test_parse_message_ref(message: str, expected: str) -> None:
    assert parse_message_ref(message) == expected


def test_result_defaults() -> None:
    result = Result()
    assert result.message_ref == ""
    assert result.code == 0
    assert result.message == ""
    assert result.recipients == ()


def test_result_carries_recipients() -> None:
    refused = SMTPError(550, "no such user", "5.1.1")
    result = Result(
        message_ref="ref-1",
        code=250,
        message="2.0.0 accepted; message_ref=ref-1",
        recipients=(
            RecipientResult("a@example.com", True),
            RecipientResult("b@example.com", False, refused),
        ),
    )
    assert result.message_ref == "ref-1"
    assert result.code == 250
    assert len(result.recipients) == 2
    assert result.recipients[0].accepted is True
    assert result.recipients[0].error is None
    assert result.recipients[1].accepted is False
    assert result.recipients[1].error is refused
    assert refused.permanent is True
