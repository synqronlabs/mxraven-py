"""The official Python SDK for the mxRaven mail runtime.

The root package exposes SMTP submission. The webhook and feedback runtime
surfaces live in :mod:`mxraven.webhook` and :mod:`mxraven.feedback`.

Control-plane administration (tenants, domains, suppressions, analytics) is out
of scope and is served by the separate mxRaven control-plane SDK.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from mxraven.client import DEFAULT_ADDRESS_PORT, Client
from mxraven.errors import (
    FeedbackError,
    InvalidSignatureError,
    MxRavenError,
    SMTPCapabilityError,
    SMTPConnectionError,
    SMTPError,
    SMTPProtocolError,
    SMTPTimeoutError,
    SMTPTransactionError,
    WebhookError,
)
from mxraven.message import (
    Attachment,
    DeliveryBy,
    DeliveryByMode,
    Envelope,
    Header,
    Message,
)
from mxraven.result import RecipientResult, Result

__all__ = [
    "DEFAULT_ADDRESS_PORT",
    "Attachment",
    "Client",
    "DeliveryBy",
    "DeliveryByMode",
    "Envelope",
    "FeedbackError",
    "Header",
    "InvalidSignatureError",
    "Message",
    "MxRavenError",
    "RecipientResult",
    "Result",
    "SMTPCapabilityError",
    "SMTPConnectionError",
    "SMTPError",
    "SMTPProtocolError",
    "SMTPTimeoutError",
    "SMTPTransactionError",
    "WebhookError",
    "__version__",
]

try:
    __version__ = version("mxraven")
except PackageNotFoundError:  # pragma: no cover - source tree without metadata
    __version__ = "0.0.0"
