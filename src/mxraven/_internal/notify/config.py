"""Shared configuration and validation for the notification clients.

Not part of the public API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from mxraven.errors import NotificationError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from mxraven._internal.notify.session import Session

__all__ = [
    "DEFAULT_CONNECT_TIMEOUT",
    "PULL_TIMEOUT",
    "RETRY_BASE",
    "RETRY_MAX",
    "Options",
    "extra_required_message",
    "load_open_session",
    "retry_delay",
    "validate_options",
]

DEFAULT_CONNECT_TIMEOUT = 10.0
"""The default connection deadline, in seconds, matching the Go and JS SDKs."""

PULL_TIMEOUT = 5.0
"""How long a pull waits for one event before the loop re-checks for closure."""

RETRY_BASE = 1.0
"""The first redelivery delay, in seconds, for a handler that raised."""

RETRY_MAX = 30.0
"""The maximum redelivery delay, in seconds."""

_WS_SCHEMES = frozenset({"ws", "wss"})
_INVALID_TOKEN_CHARS = frozenset({".", "*", ">", "/", "\\"})


@dataclass(frozen=True, slots=True)
class Options:
    """The validated connection options shared by both clients.

    Attributes:
        url: The normalized notification tier WebSocket URL.
        username: The listener API key prefix.
        secret: The listener API key secret.
        listener: The listener identifier.
        consumer: The durable consumer name.
        connect_timeout: The connection deadline in seconds.
    """

    url: str
    username: str
    secret: str
    listener: str
    consumer: str
    connect_timeout: float


def validate_options(
    *,
    url: str,
    username: str,
    secret: str,
    listener: str,
    consumer: str | None,
    connect_timeout: float,
) -> Options:
    """Validate connection options and apply defaults.

    Raises:
        ValueError: An option is missing or invalid.
    """
    resolved_url = url.strip()
    if resolved_url == "":
        message = "notify: URL is required"
        raise ValueError(message)
    scheme = urlsplit(resolved_url).scheme.lower()
    if scheme not in _WS_SCHEMES:
        message = f"notify: URL {resolved_url!r} must use the ws or wss scheme"
        raise ValueError(message)

    resolved_username = username.strip()
    if resolved_username == "":
        message = "notify: username is required"
        raise ValueError(message)
    if secret == "":
        message = "notify: secret is required"
        raise ValueError(message)

    resolved_listener = listener.strip()
    if resolved_listener == "":
        message = "notify: listener ID is required"
        raise ValueError(message)
    if not _valid_token(resolved_listener):
        message = (
            f"notify: listener ID {resolved_listener!r} must not contain "
            "'.', '*', '>', '/', '\\', or whitespace"
        )
        raise ValueError(message)

    resolved_consumer = consumer.strip() if consumer is not None else f"notify-{resolved_listener}"
    if not _valid_token(resolved_consumer):
        message = (
            f"notify: consumer name {resolved_consumer!r} must not contain "
            "'.', '*', '>', '/', '\\', or whitespace"
        )
        raise ValueError(message)

    if connect_timeout <= 0:
        message = f"notify: connect timeout must be positive, got {connect_timeout!r}"
        raise ValueError(message)

    return Options(
        url=resolved_url,
        username=resolved_username,
        secret=secret,
        listener=resolved_listener,
        consumer=resolved_consumer,
        connect_timeout=connect_timeout,
    )


def _valid_token(value: str) -> bool:
    if value == "":
        return False
    return not any(char in _INVALID_TOKEN_CHARS or char.isspace() for char in value)


def retry_delay(num_delivered: int) -> float:
    """Return the redelivery delay for a failed handler, capped at 30 seconds."""
    if num_delivered <= 1:
        return RETRY_BASE
    delay = RETRY_BASE
    for _ in range(1, num_delivered):
        if delay >= RETRY_MAX:
            break
        delay *= 2
    return min(delay, RETRY_MAX)


_EXTRA_MODULES = frozenset({"nats", "aiohttp"})


def extra_required_message() -> str:
    """Return the guidance shown when the optional ``notify`` extra is missing."""
    return "notify: the 'notify' extra is required; install mxraven[notify]"


def load_open_session() -> Callable[..., Awaitable[Session]]:
    """Import the NATS transport lazily and return its session opener.

    Raises:
        NotificationError: The optional dependency is not installed.
    """
    try:
        # PLC0415: the transport is an optional dependency, imported on demand.
        from mxraven._internal.notify.session import open_session  # noqa: PLC0415
    except ImportError as error:
        top = (error.name or "").split(".")[0]
        if top in _EXTRA_MODULES:
            raise NotificationError(extra_required_message()) from error
        raise
    return open_session
