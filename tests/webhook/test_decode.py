"""Tests for webhook payload decoding, ported from the Go SDK."""

from __future__ import annotations

import pytest

from mxraven.errors import WebhookError
from mxraven.webhook import EventType, StatusOutcome, TerminalAction, decode

INBOUND = b"""{
    "event_type": "inbound_email",
    "task_id": "task-123",
    "tenant_id": "tenant-1",
    "listener_id": "listener-1",
    "attempt": 1,
    "accepted_at_utc": 1789302600,
    "occurred_at_utc": 1789302601,
    "routing_decision": {
        "terminal_action": "TERMINAL_ACTION_TYPE_RELAY",
        "used_listener_default": false
    },
    "envelope": {"mail_from": "sender@example.net", "rcpt_to": ["support@example.com"]},
    "message": {"subject": "Hello"},
    "headers": [{"name": "From", "value": "sender@example.net"}],
    "raw_email": {
        "url": "https://raw.example.com/m.eml",
        "token_type": "Bearer",
        "access_token": "token"
    }
}"""

DELIVERY = b"""{
    "task_id": "task-9",
    "tenant_id": "tenant-1",
    "listener_id": "listener-1",
    "status": "deferred",
    "attempt": 2,
    "smtp_code": 451
}"""

STORAGE = b"""{
    "event_type": "s3_egress_status",
    "status": "delivered",
    "object_key": "2026/message.eml",
    "bucket_name": "inbound"
}"""


def test_decode_inbound() -> None:
    event = decode(INBOUND)
    assert event.type is EventType.INBOUND_EMAIL
    assert event.inbound_email is not None
    assert event.inbound_email.task_id == "task-123"
    assert event.inbound_email.envelope.rcpt_to == ("support@example.com",)
    assert event.inbound_email.headers[0].name == "From"
    assert event.inbound_email.raw_email.access_token == "token"


def test_decode_delivery_status_fallback() -> None:
    event = decode(DELIVERY)
    assert event.type is EventType.DELIVERY_STATUS
    assert event.delivery_status is not None
    assert event.delivery_status.status == StatusOutcome.DEFERRED
    assert event.delivery_status.smtp_code == 451


def test_decode_delivery_status_includes_reason() -> None:
    body = DELIVERY.replace(b'"smtp_code": 451', b'"smtp_code": 451, "reason": "smtp_transient"')
    event = decode(body)
    assert event.delivery_status is not None
    assert event.delivery_status.reason == "smtp_transient"


def test_status_outcome_includes_invalid() -> None:
    assert StatusOutcome("invalid") is StatusOutcome.INVALID


def test_terminal_action_includes_unspecified() -> None:
    assert TerminalAction("TERMINAL_ACTION_TYPE_UNSPECIFIED") is TerminalAction.UNSPECIFIED


def test_decode_storage_status() -> None:
    event = decode(STORAGE)
    assert event.type is EventType.STORAGE_STATUS
    assert event.storage_status is not None
    assert event.storage_status.object_key == "2026/message.eml"


@pytest.mark.parametrize("body", [b"{", b'{"event_type": "something_else"}', b""])
def test_decode_rejects(body: bytes) -> None:
    with pytest.raises(WebhookError):
        decode(body)
