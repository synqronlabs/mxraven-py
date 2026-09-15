"""Decode mxRaven webhook payloads into typed events."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from mxraven.errors import WebhookError
from mxraven.webhook import payload as _payload

if TYPE_CHECKING:
    from collections.abc import Mapping

    from mxraven.webhook.payload import Event

__all__ = ["decode"]

_INBOUND = "inbound_email"
_STORAGE = "s3_egress_status"
_STATUS = "status"


def decode(body: bytes) -> Event:
    """Decode a webhook body.

    Dispatch is on the payload's ``event_type``. SMTP delivery statuses carry
    no ``event_type``, so a body with a ``status`` field and no recognized
    event type is decoded as a :class:`~mxraven.webhook.DeliveryStatus`.

    Args:
        body: The raw webhook body bytes.

    Returns:
        The decoded event.

    Raises:
        WebhookError: The body is not valid JSON or is not a recognized
            payload.
    """
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        message = "webhook: decode payload"
        raise WebhookError(message) from error
    if not isinstance(data, dict):
        message = "webhook: unrecognized payload"
        raise WebhookError(message)

    event_type = data.get("event_type")
    if event_type == _INBOUND:
        return _payload.Event(_payload.EventType.INBOUND_EMAIL, inbound_email=_inbound(data))
    if event_type == _STORAGE:
        return _payload.Event(_payload.EventType.STORAGE_STATUS, storage_status=_storage(data))
    if _STATUS in data:
        return _payload.Event(_payload.EventType.DELIVERY_STATUS, delivery_status=_delivery(data))
    message = f"webhook: unrecognized payload: event_type {event_type!r}"
    raise WebhookError(message)


def _inbound(data: Mapping[str, object]) -> _payload.InboundEmail:
    routing = _payload._mapping(data, "routing_decision")
    verdicts = data.get("verdicts")
    return _payload.InboundEmail(
        event_type=_payload._str(data, "event_type"),
        task_id=_payload._str(data, "task_id"),
        tenant_id=_payload._str(data, "tenant_id"),
        listener_id=_payload._str(data, "listener_id"),
        attempt=_payload._int(data, "attempt"),
        accepted_at_utc=_payload._int(data, "accepted_at_utc"),
        occurred_at_utc=_payload._int(data, "occurred_at_utc"),
        routing_decision=_routing(routing),
        verdicts=_verdicts(verdicts) if isinstance(verdicts, dict) else None,
        envelope=_envelope(_payload._mapping(data, "envelope")),
        message=_message(_payload._mapping(data, "message")),
        headers=_headers(data.get("headers")),
        raw_email=_raw(_payload._mapping(data, "raw_email")),
    )


def _routing(data: Mapping[str, object]) -> _payload.RoutingDecision:
    return _payload.RoutingDecision(
        terminal_action=_payload._str(data, "terminal_action"),
        matched_rule_id=_payload._str(data, "matched_rule_id"),
        used_listener_default=_payload._bool(data, "used_listener_default"),
    )


def _verdicts(data: Mapping[str, object]) -> _payload.Verdicts:
    return _payload.Verdicts(
        action=_payload._str(data, "action"),
        score=_payload._float(data, "score"),
        required_score=_payload._float(data, "required_score"),
        is_spam=_payload._bool(data, "is_spam"),
        has_malware=_payload._bool(data, "has_malware"),
        malware_names=_payload._str_tuple(data, "malware_names"),
        is_skipped=_payload._bool(data, "is_skipped"),
        error=_payload._str(data, "error"),
    )


def _envelope(data: Mapping[str, object]) -> _payload.Envelope:
    return _payload.Envelope(
        mail_from=_payload._str(data, "mail_from"),
        rcpt_to=_payload._str_tuple(data, "rcpt_to"),
    )


def _message(data: Mapping[str, object]) -> _payload.MessageSummary:
    return _payload.MessageSummary(
        subject=_payload._str(data, "subject"),
        from_=_payload._str_tuple(data, "from"),
        to=_payload._str_tuple(data, "to"),
        cc=_payload._str_tuple(data, "cc"),
        message_id=_payload._str(data, "message_id"),
        date=_payload._str(data, "date"),
    )


def _headers(value: object) -> tuple[_payload.HeaderField, ...]:
    if not isinstance(value, list):
        return ()
    fields: list[_payload.HeaderField] = []
    for item in value:
        if isinstance(item, dict):
            fields.append(
                _payload.HeaderField(
                    name=_payload._str(item, "name"),
                    value=_payload._str(item, "value"),
                )
            )
    return tuple(fields)


def _raw(data: Mapping[str, object]) -> _payload.RawEmail:
    return _payload.RawEmail(
        url=_payload._str(data, "url"),
        token_type=_payload._str(data, "token_type"),
        access_token=_payload._str(data, "access_token"),
        expires_at_utc=_payload._int(data, "expires_at_utc"),
        size_bytes=_payload._int(data, "size_bytes"),
        sha256_hex=_payload._str(data, "sha256_hex"),
        content_type=_payload._str(data, "content_type"),
    )


def _delivery(data: Mapping[str, object]) -> _payload.DeliveryStatus:
    return _payload.DeliveryStatus(
        task_id=_payload._str(data, "task_id"),
        tenant_id=_payload._str(data, "tenant_id"),
        listener_id=_payload._str(data, "listener_id"),
        status=_payload._str(data, "status"),
        attempt=_payload._int(data, "attempt"),
        accepted_at_utc=_payload._int(data, "accepted_at_utc"),
        occurred_at_utc=_payload._int(data, "occurred_at_utc"),
        source_ip=_payload._str(data, "source_ip"),
        destination_domain=_payload._str(data, "destination_domain"),
        remote_host=_payload._str(data, "remote_host"),
        smtp_code=_payload._int(data, "smtp_code"),
        enhanced_status_code=_payload._str(data, "enhanced_status_code"),
        remote_response=_payload._str(data, "remote_response"),
        next_retry_at_utc=_payload._int(data, "next_retry_at_utc"),
        correlation_task_id=_payload._str(data, "correlation_task_id"),
    )


def _storage(data: Mapping[str, object]) -> _payload.StorageStatus:
    return _payload.StorageStatus(
        event_type=_payload._str(data, "event_type"),
        task_id=_payload._str(data, "task_id"),
        tenant_id=_payload._str(data, "tenant_id"),
        listener_id=_payload._str(data, "listener_id"),
        status=_payload._str(data, "status"),
        attempt=_payload._int(data, "attempt"),
        accepted_at_utc=_payload._int(data, "accepted_at_utc"),
        occurred_at_utc=_payload._int(data, "occurred_at_utc"),
        storage_ref=_payload._str(data, "storage_ref"),
        bucket_name=_payload._str(data, "bucket_name"),
        object_key=_payload._str(data, "object_key"),
        endpoint_host=_payload._str(data, "endpoint_host"),
        status_code=_payload._int(data, "status_code"),
        error_code=_payload._str(data, "error_code"),
        message=_payload._str(data, "message"),
        next_retry_at_utc=_payload._int(data, "next_retry_at_utc"),
    )
