"""mxRaven client notification events.

Consumes the durable mail events a listener publishes over NATS JetStream,
using the same API key as SMTP submission. The transport requires the optional
``notify`` extra; install ``mxraven[notify]``.

Example:
    ```python
    from mxraven.notify import NotificationClient

    with NotificationClient.connect(
        url="wss://nats.mxraven.com",
        username="mxr_tx_ab12cd34ef56",
        secret=api_key_secret,
        listener=listener_id,
    ) as client:
        for delivery in client.events():
            if delivery.event.kind == "accepted":
                handle(delivery.event.accepted)
            delivery.ack()
            break
    ```
"""

from __future__ import annotations

from mxraven.errors import DecodeError, NotificationError
from mxraven.notify.client import (
    NotificationClient,
    NotificationDelivery,
    NotificationIterator,
)
from mxraven.notify.event import (
    AttemptChannel,
    AttemptEvent,
    NotificationEvent,
    NotificationKind,
    decode_notification,
)
from mxraven.notify.handler import (
    STOP_CONSUMING,
    NotificationHandler,
    NotificationResult,
    StopConsuming,
)
from mxraven.webhook import InboundEmail

__all__ = [
    "STOP_CONSUMING",
    "AttemptChannel",
    "AttemptEvent",
    "DecodeError",
    "InboundEmail",
    "NotificationClient",
    "NotificationDelivery",
    "NotificationError",
    "NotificationEvent",
    "NotificationHandler",
    "NotificationIterator",
    "NotificationKind",
    "NotificationResult",
    "StopConsuming",
    "decode_notification",
]
