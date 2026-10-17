"""Decoded client notification event payloads.

The accepted payload is the same body mxRaven posts to a webhook endpoint, so
it is decoded into :class:`mxraven.webhook.InboundEmail`; a delivery-attempt
payload is decoded into :class:`AttemptEvent`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from mxraven.errors import DecodeError, WebhookError
from mxraven.webhook.decode import decode_inbound_email

if TYPE_CHECKING:
    from collections.abc import Mapping

    from mxraven.webhook import InboundEmail

__all__ = [
    "AttemptChannel",
    "AttemptEvent",
    "NotificationEvent",
    "NotificationKind",
    "decode_notification",
]


class NotificationKind(StrEnum):
    """The kind of notification a :class:`NotificationEvent` carries."""

    ACCEPTED = "accepted"
    ATTEMPT = "attempt"


class AttemptChannel(StrEnum):
    """The egress worker that produced an :class:`AttemptEvent`.

    The set is open on the wire: a channel this SDK does not know is preserved
    as a raw string on :attr:`AttemptEvent.channel`, which still compares equal
    to the matching member.
    """

    SMTP = "smtp"
    S3 = "s3"
    WEBHOOK = "webhook"


@dataclass(frozen=True, slots=True)
class AttemptEvent:
    """One delivery attempt for an accepted message.

    :attr:`channel` names the egress worker that produced the attempt; the
    remaining fields describe that worker's protocol and default when they do
    not apply. A rule that chains side-effect actions reports each action it
    triggers, so one message can produce more than one attempt event. Every
    attempt carries a :attr:`correlation_id` that points at the ``task_id`` of
    the accepted event for the message it stems from.

    Attributes:
        event_type: The producer's event-type marker when one is set. SMTP
            attempt events omit it.
        channel: The egress worker that produced the attempt. Compare it
            against :class:`AttemptChannel`; unknown future values are
            preserved as their raw string.
        task_id: The delivery task. For a submission listener it equals the
            accepted event's ``task_id``.
        tenant_id: The owning tenant.
        listener_id: The source listener.
        correlation_id: The ``task_id`` of the accepted event for the message,
            when one was recorded.
        status: The attempt outcome, one of :class:`mxraven.webhook.StatusOutcome`.
        attempt: The delivery attempt number, starting at 1.
        accepted_at_utc: The Unix time, in seconds, when mxRaven accepted the
            message. Zero when the producer does not report it.
        occurred_at_utc: The Unix time, in seconds, when the attempt was built.
        source_ip: The SMTP egress source address, when known.
        destination_domain: The recipient domain for an SMTP attempt.
        remote_host: The remote MTA hostname, when known.
        smtp_code: The remote SMTP reply code.
        enhanced_status_code: The RFC 3463 enhanced status code.
        reason: The bounded, machine-readable delivery classification.
        remote_response: The remote reply text, truncated by the server.
        correlation_task_id: Links a generated DSN back to its original task.
        storage_ref: The storage integration identifier for an ``s3`` attempt.
        bucket_name: The destination bucket for an ``s3`` attempt.
        object_key: The stored object key for an ``s3`` attempt.
        endpoint_host: The storage endpoint host for an ``s3`` attempt.
        error_code: The storage provider error code, when one was returned.
        status_code: The storage or webhook HTTP response status.
        message: The failure description, truncated by the server.
        next_retry_at_utc: The Unix time, in seconds, of the next attempt when
            the attempt will be retried.
        retry_after_ms: The webhook retry-after hint, in milliseconds.
        target_url_host: The webhook target host.
        webhook_ref: The webhook endpoint identifier.
        signing_key_kid: The key ID used to sign the webhook attempt.
        raw_token_ttl_seconds: The raw-message token lifetime the webhook
            attempt used.
    """

    channel: str = ""
    status: str = ""
    event_type: str = ""
    task_id: str = ""
    tenant_id: str = ""
    listener_id: str = ""
    correlation_id: str = ""
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
    correlation_task_id: str = ""
    storage_ref: str = ""
    bucket_name: str = ""
    object_key: str = ""
    endpoint_host: str = ""
    error_code: str = ""
    status_code: int = 0
    message: str = ""
    next_retry_at_utc: int = 0
    retry_after_ms: int = 0
    target_url_host: str = ""
    webhook_ref: str = ""
    signing_key_kid: str = ""
    raw_token_ttl_seconds: int = 0


@dataclass(frozen=True, slots=True)
class NotificationEvent:
    """A decoded client notification.

    Exactly one of :attr:`accepted` or :attr:`attempt` is set, matching
    :attr:`kind`.
    """

    kind: NotificationKind
    accepted: InboundEmail | None = None
    attempt: AttemptEvent | None = None


_ACCEPTED = "accepted"
_ATTEMPT = "attempt"
_INBOUND = "inbound_email"


def decode_notification(subject: str, body: bytes) -> NotificationEvent:
    """Decode one notification body.

    The event kind is the final token of `subject`. When the subject is
    unavailable or unrecognized, the body shape is probed instead.

    Args:
        subject: The notification subject the body was published to.
        body: The raw JSON body bytes.

    Returns:
        The decoded notification.

    Raises:
        DecodeError: The body is not valid JSON, has a field of the wrong type,
            or cannot be identified.
    """
    source = _parse(subject, body)
    kind = _subject_kind(subject)
    if kind == _ACCEPTED:
        return NotificationEvent(NotificationKind.ACCEPTED, accepted=_accepted(subject, source))
    if kind == _ATTEMPT:
        return NotificationEvent(NotificationKind.ATTEMPT, attempt=_attempt(subject, source))

    if source.get("event_type") == _INBOUND:
        return NotificationEvent(NotificationKind.ACCEPTED, accepted=_accepted(subject, source))
    channel = source.get("channel")
    if isinstance(channel, str) and channel != "":
        return NotificationEvent(NotificationKind.ATTEMPT, attempt=_attempt(subject, source))
    message = "unrecognized notification body"
    raise DecodeError(subject, message)


def _subject_kind(subject: str) -> str:
    trimmed = subject.strip()
    index = trimmed.rfind(".")
    return trimmed[index + 1 :].strip() if index >= 0 else trimmed


def _parse(subject: str, body: bytes) -> Mapping[str, object]:
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        message = "notification payload"
        raise DecodeError(subject, message) from error
    if not isinstance(data, dict):
        message = "unrecognized notification body"
        raise DecodeError(subject, message)
    return data


def _accepted(subject: str, data: Mapping[str, object]) -> InboundEmail:
    # The accepted body is the webhook inbound-email contract, so it is decoded
    # by the webhook decoder rather than re-parsed here.
    try:
        return decode_inbound_email(data)
    except WebhookError as error:
        message = "accepted event"
        raise DecodeError(subject, message) from error


def _attempt(subject: str, data: Mapping[str, object]) -> AttemptEvent:
    return AttemptEvent(
        channel=_required_str(subject, data, "channel"),
        status=_required_str(subject, data, "status"),
        event_type=_str(subject, data, "event_type"),
        task_id=_str(subject, data, "task_id"),
        tenant_id=_str(subject, data, "tenant_id"),
        listener_id=_str(subject, data, "listener_id"),
        correlation_id=_str(subject, data, "correlation_id"),
        attempt=_int(subject, data, "attempt"),
        accepted_at_utc=_int(subject, data, "accepted_at_utc"),
        occurred_at_utc=_int(subject, data, "occurred_at_utc"),
        source_ip=_str(subject, data, "source_ip"),
        destination_domain=_str(subject, data, "destination_domain"),
        remote_host=_str(subject, data, "remote_host"),
        smtp_code=_int(subject, data, "smtp_code"),
        enhanced_status_code=_str(subject, data, "enhanced_status_code"),
        reason=_str(subject, data, "reason"),
        remote_response=_str(subject, data, "remote_response"),
        correlation_task_id=_str(subject, data, "correlation_task_id"),
        storage_ref=_str(subject, data, "storage_ref"),
        bucket_name=_str(subject, data, "bucket_name"),
        object_key=_str(subject, data, "object_key"),
        endpoint_host=_str(subject, data, "endpoint_host"),
        error_code=_str(subject, data, "error_code"),
        status_code=_int(subject, data, "status_code"),
        message=_str(subject, data, "message"),
        next_retry_at_utc=_int(subject, data, "next_retry_at_utc"),
        retry_after_ms=_int(subject, data, "retry_after_ms"),
        target_url_host=_str(subject, data, "target_url_host"),
        webhook_ref=_str(subject, data, "webhook_ref"),
        signing_key_kid=_str(subject, data, "signing_key_kid"),
        raw_token_ttl_seconds=_int(subject, data, "raw_token_ttl_seconds"),
    )


def _str(subject: str, data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    message = f"field {key!r} must be a string"
    raise DecodeError(subject, message)


def _required_str(subject: str, data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    if isinstance(value, str) and value != "":
        return value
    message = f"field {key!r} must be a non-empty string"
    raise DecodeError(subject, message)


def _int(subject: str, data: Mapping[str, object], key: str) -> int:
    value = data.get(key)
    if value is None:
        return 0
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    message = f"field {key!r} must be an integer"
    raise DecodeError(subject, message)
