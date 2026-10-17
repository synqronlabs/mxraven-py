"""Notification payload tests, ported from mxraven-go and mxraven-js."""

from __future__ import annotations

import json

import pytest

from mxraven.errors import DecodeError
from mxraven.notify import AttemptChannel, NotificationKind
from mxraven.notify.event import decode_notification
from mxraven.webhook import StatusOutcome

ACCEPTED = "tenant.tenant-1.listener.listener-1.events.accepted"
ATTEMPT = "tenant.tenant-1.listener.listener-1.events.attempt"

ACCEPTED_BODY = json.dumps(
    {
        "event_type": "inbound_email",
        "task_id": "task-1",
        "tenant_id": "tenant-1",
        "listener_id": "listener-1",
        "attempt": 1,
        "accepted_at_utc": 1700000000,
        "occurred_at_utc": 1700000001,
        "routing_decision": {
            "terminal_action": "TERMINAL_ACTION_TYPE_DELIVER_WEBHOOK",
            "used_listener_default": False,
        },
        "verdicts": {
            "action": "accept",
            "score": 0,
            "required_score": 5,
            "is_spam": False,
            "has_malware": False,
            "malware_names": [],
            "is_skipped": True,
            "error": "",
        },
        "envelope": {"mail_from": "sender@example.com", "rcpt_to": ["rcpt@example.com"]},
        "message": {"subject": "hi", "message_id": "<1@example.com>"},
        "headers": [{"name": "Subject", "value": "hi"}],
        "raw_email": {
            "url": "https://raw.example/messages/task-1.eml",
            "token_type": "Bearer",
            "access_token": "secret-token",
            "expires_at_utc": 1700100000,
            "size_bytes": 12,
            "sha256_hex": "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
            "content_type": "text/plain",
        },
    }
).encode()

SMTP_ATTEMPT_BODY = json.dumps(
    {
        "channel": "smtp",
        "task_id": "task-1",
        "tenant_id": "tenant-1",
        "listener_id": "listener-1",
        "correlation_id": "accepted-1",
        "status": "deferred",
        "attempt": 3,
        "accepted_at_utc": 1700000000,
        "occurred_at_utc": 1700000001,
        "source_ip": "192.0.2.1",
        "destination_domain": "example.com",
        "remote_host": "mx.example.com",
        "smtp_code": 451,
        "enhanced_status_code": "4.7.1",
        "reason": "smtp_transient",
        "remote_response": "try later",
        "next_retry_at_utc": 1700000060,
        "correlation_task_id": "corr-1",
    }
).encode()

S3_ATTEMPT_BODY = json.dumps(
    {
        "event_type": "s3_egress_status",
        "channel": "s3",
        "task_id": "task-1",
        "tenant_id": "tenant-1",
        "listener_id": "listener-1",
        "correlation_id": "accepted-1",
        "status": "delivered",
        "attempt": 2,
        "accepted_at_utc": 1700000000,
        "occurred_at_utc": 1700000001,
        "storage_ref": "storage-1",
        "bucket_name": "bucket",
        "object_key": "inbound/a.eml",
        "endpoint_host": "s3.example.com",
        "status_code": 200,
    }
).encode()

WEBHOOK_ATTEMPT_BODY = json.dumps(
    {
        "event_type": "webhook_egress_status",
        "channel": "webhook",
        "task_id": "task-1",
        "tenant_id": "tenant-1",
        "listener_id": "listener-1",
        "correlation_id": "accepted-1",
        "status": "invalid",
        "attempt": 1,
        "status_code": 500,
        "message": "boom",
        "retry_after_ms": 1000,
        "occurred_at_utc": 1700000001,
        "target_url_host": "hooks.example.com",
        "webhook_ref": "hook-1",
        "signing_key_kid": "kid-1",
        "raw_token_ttl_seconds": 90000,
    }
).encode()


def test_decodes_accepted_as_the_webhook_payload() -> None:
    event = decode_notification(ACCEPTED, ACCEPTED_BODY)
    assert event.kind is NotificationKind.ACCEPTED
    assert event.attempt is None
    assert event.accepted is not None
    assert event.accepted.task_id == "task-1"
    assert event.accepted.raw_email.access_token == "secret-token"
    assert event.accepted.verdicts is not None
    assert event.accepted.verdicts.is_skipped is True


@pytest.mark.parametrize(
    ("body", "channel", "status"),
    [
        (SMTP_ATTEMPT_BODY, AttemptChannel.SMTP, StatusOutcome.DEFERRED),
        (S3_ATTEMPT_BODY, AttemptChannel.S3, StatusOutcome.DELIVERED),
        (WEBHOOK_ATTEMPT_BODY, AttemptChannel.WEBHOOK, StatusOutcome.INVALID),
    ],
)
def test_decodes_attempt_channels(
    body: bytes, channel: AttemptChannel, status: StatusOutcome
) -> None:
    event = decode_notification(ATTEMPT, body)
    assert event.kind is NotificationKind.ATTEMPT
    assert event.accepted is None
    assert event.attempt is not None
    assert event.attempt.channel == channel
    assert event.attempt.status == status
    assert event.attempt.correlation_id == "accepted-1"


def test_maps_every_smtp_attempt_field() -> None:
    event = decode_notification(ATTEMPT, SMTP_ATTEMPT_BODY)
    assert event.attempt is not None
    attempt = event.attempt
    assert attempt.channel == "smtp"
    assert attempt.task_id == "task-1"
    assert attempt.tenant_id == "tenant-1"
    assert attempt.listener_id == "listener-1"
    assert attempt.correlation_id == "accepted-1"
    assert attempt.status == "deferred"
    assert attempt.attempt == 3
    assert attempt.accepted_at_utc == 1700000000
    assert attempt.occurred_at_utc == 1700000001
    assert attempt.source_ip == "192.0.2.1"
    assert attempt.destination_domain == "example.com"
    assert attempt.remote_host == "mx.example.com"
    assert attempt.smtp_code == 451
    assert attempt.enhanced_status_code == "4.7.1"
    assert attempt.reason == "smtp_transient"
    assert attempt.remote_response == "try later"
    assert attempt.next_retry_at_utc == 1700000060
    assert attempt.correlation_task_id == "corr-1"


def test_maps_every_webhook_attempt_field() -> None:
    event = decode_notification(ATTEMPT, WEBHOOK_ATTEMPT_BODY)
    assert event.attempt is not None
    attempt = event.attempt
    assert attempt.event_type == "webhook_egress_status"
    assert attempt.channel == "webhook"
    assert attempt.status == "invalid"
    assert attempt.status_code == 500
    assert attempt.message == "boom"
    assert attempt.retry_after_ms == 1000
    assert attempt.target_url_host == "hooks.example.com"
    assert attempt.webhook_ref == "hook-1"
    assert attempt.signing_key_kid == "kid-1"
    assert attempt.raw_token_ttl_seconds == 90000


@pytest.mark.parametrize("subject", ["", "tenant.tenant-1.listener.listener-1.events.unknown"])
@pytest.mark.parametrize("body", [ACCEPTED_BODY, S3_ATTEMPT_BODY])
def test_falls_back_to_the_body_shape(subject: str, body: bytes) -> None:
    assert decode_notification(subject, body) is not None


def test_reports_invalid_json_with_the_subject() -> None:
    with pytest.raises(DecodeError) as info:
        decode_notification(ACCEPTED, b"{")
    assert info.value.subject == ACCEPTED


def test_reports_an_unrecognized_body() -> None:
    with pytest.raises(DecodeError):
        decode_notification("x.events.unknown", json.dumps({"foo": "bar"}).encode())


def test_reports_a_field_with_the_wrong_type() -> None:
    body = json.dumps(
        {
            "channel": "smtp",
            "task_id": "task-1",
            "tenant_id": "tenant-1",
            "listener_id": "listener-1",
            "status": "delivered",
            "attempt": "one",
            "occurred_at_utc": 1700000001,
        }
    ).encode()
    with pytest.raises(DecodeError):
        decode_notification(ATTEMPT, body)


@pytest.mark.parametrize(
    ("subject", "kind"),
    [
        ("tenant.tenant-1.listener.listener-1.events.accepted", NotificationKind.ACCEPTED),
        ("  spaced.attempt  ", NotificationKind.ATTEMPT),
    ],
)
def test_subject_kind_dispatch(subject: str, kind: NotificationKind) -> None:
    body = ACCEPTED_BODY if kind is NotificationKind.ACCEPTED else SMTP_ATTEMPT_BODY
    assert decode_notification(subject, body).kind is kind


def test_decodes_accepted_without_event_type() -> None:
    payload = json.loads(ACCEPTED_BODY)
    del payload["event_type"]
    event = decode_notification(ACCEPTED, json.dumps(payload).encode())
    assert event.kind is NotificationKind.ACCEPTED
    assert event.accepted is not None
    assert event.accepted.task_id == "task-1"


def test_decodes_attempt_without_subject() -> None:
    event = decode_notification("noseparator", S3_ATTEMPT_BODY)
    assert event.kind is NotificationKind.ATTEMPT
