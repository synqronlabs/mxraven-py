"""Tests for the public exception hierarchy."""

from __future__ import annotations

import pytest

from mxraven.errors import (
    FeedbackError,
    InvalidSignatureError,
    MxRavenError,
    SMTPConnectionError,
    SMTPError,
    SMTPProtocolError,
    SMTPTimeoutError,
    SMTPTransactionError,
    WebhookError,
)


@pytest.mark.parametrize(
    ("code", "permanent", "transient"),
    [
        (399, False, False),
        (400, False, True),
        (499, False, True),
        (500, True, False),
        (599, True, False),
        (600, False, False),
    ],
)
def test_smtp_error_predicates(code: int, permanent: bool, transient: bool) -> None:
    error = SMTPError(code, "nope")
    assert error.permanent is permanent
    assert error.transient is transient


def test_smtp_error_formatting() -> None:
    assert str(SMTPError(421, "Try later")) == "smtp 421: Try later"
    assert str(SMTPError(550, "No", "5.1.1")) == "smtp 550 5.1.1: No"


def test_smtp_transaction_error_carries_result() -> None:
    marker = object()
    error = SMTPTransactionError("all recipients rejected", marker)  # type: ignore[arg-type]
    assert error.result is marker
    assert isinstance(error, MxRavenError)


def test_connection_errors_are_mxraven_errors() -> None:
    assert issubclass(SMTPConnectionError, MxRavenError)
    assert issubclass(SMTPTimeoutError, SMTPConnectionError)
    assert issubclass(SMTPProtocolError, MxRavenError)


def test_webhook_error_hierarchy() -> None:
    assert issubclass(InvalidSignatureError, WebhookError)
    assert issubclass(WebhookError, MxRavenError)


@pytest.mark.parametrize(
    ("status", "retryable"),
    [
        (400, False),
        (404, False),
        (429, True),
        (500, True),
        (503, True),
    ],
)
def test_feedback_error_retryable(status: int, retryable: bool) -> None:
    assert FeedbackError(status).retryable is retryable


def test_feedback_error_formatting() -> None:
    assert str(FeedbackError(404)) == "feedback: request failed with status 404"
    assert (
        str(FeedbackError(404, "message evidence not found"))
        == "feedback: request failed with status 404: message evidence not found"
    )
