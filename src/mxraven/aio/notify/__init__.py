"""Asynchronous mxRaven client notification events.

The asyncio counterpart of :mod:`mxraven.notify`, with the same payloads and
error surface. The transport requires the optional ``notify`` extra; install
``mxraven[notify]``.
"""

from __future__ import annotations

from mxraven.aio.notify.client import (
    NotificationClient,
    NotificationDelivery,
    NotificationIterator,
)
from mxraven.errors import DecodeError, NotificationError
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
