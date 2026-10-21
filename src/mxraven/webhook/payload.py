"""Typed mxRaven webhook payloads."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from mxraven._internal.http import DEFAULT_ASYNC_TRANSPORT, DEFAULT_SYNC_TRANSPORT
from mxraven.errors import WebhookError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from mxraven._internal.http import AsyncTransport, SyncTransport

__all__ = [
    "DeliveryStatus",
    "Envelope",
    "Event",
    "EventType",
    "HeaderField",
    "InboundEmail",
    "MessageSummary",
    "RawEmail",
    "RoutingDecision",
    "StatusOutcome",
    "StorageStatus",
    "TerminalAction",
    "Verdicts",
]

_MAX_RAW_EMAIL_BYTES = 64 << 20
_HTTP_OK = 200
_HTTP_SUCCESS_MAX = 300


class EventType(StrEnum):
    """The shape of a decoded webhook payload."""

    INBOUND_EMAIL = "inbound_email"
    STORAGE_STATUS = "s3_egress_status"
    DELIVERY_STATUS = "delivery_status"


class StatusOutcome(StrEnum):
    """The lifecycle state reported by a delivery status webhook.

    The set is open on the wire: a status this SDK does not know is still
    decoded and preserved as its raw string.
    """

    ATTEMPTED = "attempted"
    DELIVERED = "delivered"
    DEFERRED = "deferred"
    FAILED = "failed"
    EXPIRED = "expired"
    SUPPRESSED = "suppressed"
    INVALID = "invalid"


class TerminalAction(StrEnum):
    """The final routing action recorded for an inbound message."""

    UNSPECIFIED = "TERMINAL_ACTION_TYPE_UNSPECIFIED"
    DELIVER_DEDICATED = "TERMINAL_ACTION_TYPE_DELIVER_DEDICATED"
    SMARTHOST_RELAY = "TERMINAL_ACTION_TYPE_SMARTHOST_RELAY"
    DELIVER_WEBHOOK = "TERMINAL_ACTION_TYPE_DELIVER_WEBHOOK"
    SMTP_FORWARD = "TERMINAL_ACTION_TYPE_SMTP_FORWARD"
    RELAY = "TERMINAL_ACTION_TYPE_RELAY"
    S3_STORE = "TERMINAL_ACTION_TYPE_S3_STORE"
    AUTO_REPLY = "TERMINAL_ACTION_TYPE_AUTO_REPLY"
    DROP = "TERMINAL_ACTION_TYPE_DROP"
    REJECT = "TERMINAL_ACTION_TYPE_REJECT"
    DELIVER = "TERMINAL_ACTION_TYPE_DELIVER"
    SRS_RETURN = "TERMINAL_ACTION_TYPE_SRS_RETURN"
    LOCAL_DSN = "TERMINAL_ACTION_TYPE_LOCAL_DSN"


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """The final routing outcome for an inbound message.

    Attributes:
        terminal_action: The final terminal action. Compare it against the
            :class:`TerminalAction` members; unknown future values are
            preserved as their raw string.
        matched_rule_id: The rule that selected the action, when one matched.
        used_listener_default: Whether the listener default was used.
    """

    terminal_action: str = ""
    matched_rule_id: str = ""
    used_listener_default: bool = False


@dataclass(frozen=True, slots=True)
class Verdicts:
    """Spam and malware scan results."""

    action: str = ""
    score: float = 0.0
    required_score: float = 0.0
    is_spam: bool = False
    has_malware: bool = False
    malware_names: tuple[str, ...] = ()
    is_skipped: bool = False
    error: str = ""


@dataclass(frozen=True, slots=True)
class Envelope:
    """The SMTP envelope of a message."""

    mail_from: str = ""
    rcpt_to: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MessageSummary:
    """A summary of the parsed message headers."""

    subject: str = ""
    from_: tuple[str, ...] = ()
    to: tuple[str, ...] = ()
    cc: tuple[str, ...] = ()
    message_id: str = ""
    date: str = ""


@dataclass(frozen=True, slots=True)
class HeaderField:
    """One message header occurrence."""

    name: str
    value: str


@dataclass(frozen=True, slots=True)
class RawEmail:
    """Time-limited access to the raw RFC 822 message."""

    url: str = ""
    token_type: str = ""
    access_token: str = ""
    expires_at_utc: int = 0
    size_bytes: int = 0
    sha256_hex: str = ""
    content_type: str = ""

    def fetch(self, *, timeout: float = 60.0, transport: SyncTransport | None = None) -> bytes:
        """Download and verify the raw message.

        Args:
            timeout: The request timeout in seconds.
            transport: An optional transport to use instead of the default.

        Returns:
            The verified raw message bytes.

        Raises:
            WebhookError: The URL or token is missing, the response is not 200,
                the size does not match, or the SHA-256 digest does not match.

        Note:
            The raw-email access token is a secret; do not log it.
        """
        chosen = transport or DEFAULT_SYNC_TRANSPORT
        _require_raw_fields(self)
        response = chosen.request(
            "GET",
            self.url.strip(),
            headers={"Authorization": _authorization(self)},
            body=None,
            timeout=timeout,
        )
        return _verify_raw(self, response.status, response.reason, response.body)

    async def fetch_async(
        self, *, timeout: float = 60.0, transport: AsyncTransport | None = None
    ) -> bytes:
        """Asynchronously download and verify the raw message.

        Args:
            timeout: The request timeout in seconds.
            transport: An optional transport to use instead of the default.

        Returns:
            The verified raw message bytes.

        Raises:
            WebhookError: The URL or token is missing, the response is not 200,
                the size does not match, or the SHA-256 digest does not match.

        Note:
            The raw-email access token is a secret; do not log it.
        """
        chosen = transport or DEFAULT_ASYNC_TRANSPORT
        _require_raw_fields(self)
        response = await chosen.request(
            "GET",
            self.url.strip(),
            headers={"Authorization": _authorization(self)},
            body=None,
            timeout=timeout,
        )
        return _verify_raw(self, response.status, response.reason, response.body)


def _authorization(raw: RawEmail) -> str:
    scheme = raw.token_type.strip() or "Bearer"
    return f"{scheme} {raw.access_token}"


def _require_raw_fields(raw: RawEmail) -> None:
    if not raw.url.strip():
        message = "webhook: raw email URL is empty"
        raise WebhookError(message)
    if not raw.access_token.strip():
        message = "webhook: raw email access token is empty"
        raise WebhookError(message)


def _verify_raw(raw: RawEmail, status: int, reason: str, body: bytes) -> bytes:
    if not _HTTP_OK <= status < _HTTP_SUCCESS_MAX:
        message = f"webhook: fetch raw email: unexpected status {status} {reason}".strip()
        raise WebhookError(message)
    if len(body) > _MAX_RAW_EMAIL_BYTES:
        message = "webhook: raw email exceeds the maximum size"
        raise WebhookError(message)
    if raw.size_bytes > 0 and len(body) > raw.size_bytes:
        message = f"webhook: raw email exceeds {raw.size_bytes} bytes"
        raise WebhookError(message)
    if raw.size_bytes > 0 and len(body) != raw.size_bytes:
        message = f"webhook: raw email size = {len(body)} bytes, want {raw.size_bytes}"
        raise WebhookError(message)
    digest = raw.sha256_hex.strip()
    if digest and hashlib.sha256(body).hexdigest() != digest.lower():
        message = "webhook: raw email SHA-256 mismatch"
        raise WebhookError(message)
    return body


@dataclass(frozen=True, slots=True)
class InboundEmail:
    """The ``DELIVER_WEBHOOK`` payload: a complete inbound message."""

    event_type: str = ""
    task_id: str = ""
    tenant_id: str = ""
    listener_id: str = ""
    message_ref: str = ""
    attempt: int = 0
    accepted_at_utc: int = 0
    occurred_at_utc: int = 0
    routing_decision: RoutingDecision | None = None
    verdicts: Verdicts | None = None
    envelope: Envelope = field(default_factory=Envelope)
    message: MessageSummary = field(default_factory=MessageSummary)
    headers: tuple[HeaderField, ...] = ()
    raw_email: RawEmail = field(default_factory=RawEmail)


@dataclass(frozen=True, slots=True)
class DeliveryStatus:
    """The SMTP ``NOTIFY_WEBHOOK`` payload."""

    task_id: str = ""
    tenant_id: str = ""
    listener_id: str = ""
    message_ref: str = ""
    status: str = ""
    attempt: int = 0
    accepted_at_utc: int = 0
    occurred_at_utc: int = 0
    source_ip: str = ""
    destination_domain: str = ""
    remote_host: str = ""
    smtp_code: int = 0
    enhanced_status_code: str = ""
    reason: str = ""
    remote_response: str = ""
    next_retry_at_utc: int = 0
    correlation_task_id: str = ""


@dataclass(frozen=True, slots=True)
class StorageStatus:
    """The object-storage ``NOTIFY_WEBHOOK`` payload."""

    event_type: str = ""
    task_id: str = ""
    tenant_id: str = ""
    listener_id: str = ""
    message_ref: str = ""
    status: str = ""
    attempt: int = 0
    accepted_at_utc: int = 0
    occurred_at_utc: int = 0
    storage_ref: str = ""
    bucket_name: str = ""
    object_key: str = ""
    endpoint_host: str = ""
    status_code: int = 0
    error_code: str = ""
    message: str = ""
    next_retry_at_utc: int = 0


@dataclass(frozen=True, slots=True)
class Event:
    """A decoded webhook delivery.

    Exactly one of `inbound_email`, `delivery_status`, or `storage_status` is
    set, matching `type`.
    """

    type: EventType
    inbound_email: InboundEmail | None = None
    delivery_status: DeliveryStatus | None = None
    storage_status: StorageStatus | None = None


def _str(data: Mapping[str, object], key: str, default: str = "") -> str:
    value = data.get(key)
    return value if isinstance(value, str) else default


def _int(data: Mapping[str, object], key: str, default: int = 0) -> int:
    value = data.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _bool(data: Mapping[str, object], key: str, *, default: bool = False) -> bool:
    value = data.get(key)
    return value if isinstance(value, bool) else default


def _float(data: Mapping[str, object], key: str, default: float = 0.0) -> float:
    value = data.get(key)
    return (
        float(value) if isinstance(value, int | float) and not isinstance(value, bool) else default
    )


def _str_tuple(data: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = data.get(key)
    if isinstance(value, list):
        return tuple(item for item in value if isinstance(item, str))
    return ()


def _mapping(data: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = data.get(key)
    return value if isinstance(value, dict) else {}
