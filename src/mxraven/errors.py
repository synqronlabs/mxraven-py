"""Exception hierarchy for the mxRaven SDK.

Every error the SDK raises for a runtime or service condition derives from
:class:`MxRavenError`. Programmer errors such as invalid constructor arguments
raise :class:`ValueError` or :class:`TypeError` instead, so callers can reserve
``except MxRavenError`` for conditions they may need to handle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mxraven.result import Result

__all__ = [
    "DecodeError",
    "FeedbackError",
    "InvalidSignatureError",
    "MxRavenError",
    "NotificationError",
    "SMTPCapabilityError",
    "SMTPConnectionError",
    "SMTPError",
    "SMTPProtocolError",
    "SMTPTimeoutError",
    "SMTPTransactionError",
    "WebhookError",
]

_TRANSIENT_MIN = 400
_TRANSIENT_MAX = 500
_PERMANENT_MIN = 500
_PERMANENT_MAX = 600
_TOO_MANY_REQUESTS = 429
_SERVER_ERROR_MIN = 500


class MxRavenError(Exception):
    """Base class for every error raised by the mxRaven SDK."""


class SMTPConnectionError(MxRavenError):
    """A connection could not be established or was lost.

    The underlying socket or TLS failure is preserved as :attr:`__cause__`.
    """


class SMTPTimeoutError(SMTPConnectionError):
    """An SMTP operation exceeded its deadline."""


class SMTPProtocolError(MxRavenError):
    """The server sent a reply the client could not parse."""


class SMTPCapabilityError(MxRavenError):
    """The server does not support a feature the message requires."""


class SMTPError(MxRavenError):
    """An SMTP reply that rejected a command.

    Inspect :attr:`code` or :attr:`enhanced_code` to make a delivery decision.
    :attr:`message` is the server's human-readable text and is not stable.
    """

    def __init__(self, code: int, message: str, enhanced_code: str = "") -> None:
        """Initialize the error.

        Args:
            code: The three-digit SMTP reply code.
            message: The server's reply text.
            enhanced_code: The RFC 3463 ``X.Y.Z`` enhanced status code, when the
                server supplied one.
        """
        self.code = code
        self.enhanced_code = enhanced_code
        self.message = message
        super().__init__(_format_smtp_error(code, enhanced_code, message))

    @property
    def permanent(self) -> bool:
        """Whether the failure is permanent (5xx); retrying is unlikely to help."""
        return _PERMANENT_MIN <= self.code < _PERMANENT_MAX

    @property
    def transient(self) -> bool:
        """Whether the failure is transient (4xx); the message may be retried."""
        return _TRANSIENT_MIN <= self.code < _TRANSIENT_MAX


def _format_smtp_error(code: int, enhanced_code: str, message: str) -> str:
    if enhanced_code:
        return f"smtp {code} {enhanced_code}: {message}"
    return f"smtp {code}: {message}"


class SMTPTransactionError(MxRavenError):
    """Every envelope recipient was rejected.

    The per-recipient detail is available on :attr:`result`.
    """

    def __init__(self, message: str, result: Result) -> None:
        """Initialize the error.

        Args:
            message: A human-readable summary.
            result: The transaction result, including each recipient's outcome.
        """
        self.result = result
        super().__init__(message)


class WebhookError(MxRavenError):
    """Base class for webhook verification and decoding failures."""


class InvalidSignatureError(WebhookError):
    """A webhook request's HMAC signature did not match."""


class FeedbackError(MxRavenError):
    """The feedback service returned a non-success response."""

    def __init__(self, status_code: int, message: str = "") -> None:
        """Initialize the error.

        Args:
            status_code: The HTTP response status.
            message: The service's ``error`` field, or the HTTP status text.
        """
        self.status_code = status_code
        self.message = message
        detail = message.strip()
        text = f"feedback: request failed with status {status_code}"
        if detail:
            text = f"{text}: {detail}"
        super().__init__(text)

    @property
    def detail(self) -> str:
        """The service's error detail, if any. Alias of :attr:`message`."""
        return self.message

    @property
    def retryable(self) -> bool:
        """Whether the request may succeed if retried later (429 or 5xx)."""
        return self.status_code == _TOO_MANY_REQUESTS or self.status_code >= _SERVER_ERROR_MIN


class NotificationError(MxRavenError):
    """A failure connecting to or consuming the client notification stream.

    The underlying transport or JetStream failure is preserved as
    :attr:`__cause__`. Most connection failures are transient and are retried
    automatically; this error is only raised when an operation cannot continue.
    """


class DecodeError(NotificationError):
    """A notification body that does not match the SDK's payload model.

    This normally means the SDK is older than the server that produced the
    event. The event is terminated rather than redelivered, because redelivery
    can never succeed. When an error handler is configured, it receives the
    error and consumption continues; otherwise consumption stops and this error
    is raised.
    """

    def __init__(self, subject: str, message: str = "") -> None:
        """Initialize the error.

        Args:
            subject: The notification subject the undecodable body was
                published to.
            message: A short description of the decode failure.
        """
        self.subject = subject
        self.message = message
        detail = message.strip()
        text = f"notify: decode {subject!r}"
        if detail:
            text = f"{text}: {detail}"
        super().__init__(text)
