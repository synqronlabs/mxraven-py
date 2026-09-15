"""mxRaven webhook verification and decoding.

Verifies the ``X-MxRaven-*`` HMAC-SHA256 signature over the canonical request
string and decodes ``DELIVER_WEBHOOK`` (inbound email) and ``NOTIFY_WEBHOOK``
(SMTP or object-storage delivery status) payloads.

Example:
    ```python
    from mxraven.webhook import Verifier, WebhookRequest

    verifier = Verifier(secret=signing_secret)
    request = WebhookRequest.create(method, url, headers, body)
    event = verifier.verify_and_decode(request)
    ```
"""

from __future__ import annotations

from mxraven.errors import InvalidSignatureError, WebhookError
from mxraven.webhook.decode import decode
from mxraven.webhook.payload import (
    DeliveryStatus,
    Envelope,
    Event,
    EventType,
    HeaderField,
    InboundEmail,
    MessageSummary,
    RawEmail,
    RoutingDecision,
    StatusOutcome,
    StorageStatus,
    TerminalAction,
    Verdicts,
)
from mxraven.webhook.request import WebhookRequest
from mxraven.webhook.verifier import Verifier

__all__ = [
    "DeliveryStatus",
    "Envelope",
    "Event",
    "EventType",
    "HeaderField",
    "InboundEmail",
    "InvalidSignatureError",
    "MessageSummary",
    "RawEmail",
    "RoutingDecision",
    "StatusOutcome",
    "StorageStatus",
    "TerminalAction",
    "Verdicts",
    "Verifier",
    "WebhookError",
    "WebhookRequest",
    "decode",
]
