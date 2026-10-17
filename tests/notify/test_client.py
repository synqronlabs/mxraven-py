"""Synchronous notification client tests against a threaded fake NATS server."""

from __future__ import annotations

import json
import threading

import pytest

from mxraven.errors import NotificationError
from mxraven.notify import (
    STOP_CONSUMING,
    AttemptChannel,
    NotificationClient,
    NotificationEvent,
    NotificationKind,
    NotificationResult,
)
from tests.notify.fake_nats import FakeNatsEvent, ThreadedFakeNatsServer

LISTENER = "listener-1"
USERNAME = "mxr_tx_ab12cd34ef56"
SECRET = "api-key-secret"

ACCEPTED_BODY = json.dumps(
    {
        "event_type": "inbound_email",
        "task_id": "task-1",
        "tenant_id": "tenant-1",
        "listener_id": LISTENER,
        "attempt": 1,
        "accepted_at_utc": 1700000000,
        "occurred_at_utc": 1700000001,
        "routing_decision": {
            "terminal_action": "TERMINAL_ACTION_TYPE_DELIVER_WEBHOOK",
            "used_listener_default": False,
        },
        "envelope": {"mail_from": "sender@example.com", "rcpt_to": ["rcpt@example.com"]},
        "message": {"subject": "hi"},
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
        "listener_id": LISTENER,
        "correlation_id": "accepted-1",
        "status": "deferred",
        "attempt": 3,
        "accepted_at_utc": 1700000000,
        "occurred_at_utc": 1700000001,
        "smtp_code": 451,
        "remote_response": "try later",
    }
).encode()


def event(kind: str, body: bytes) -> FakeNatsEvent:
    return FakeNatsEvent(f"tenant.tenant-1.listener.{LISTENER}.events.{kind}", body)


def connect(server: ThreadedFakeNatsServer) -> NotificationClient:
    return NotificationClient.connect(
        url=server.url,
        username=USERNAME,
        secret=SECRET,
        listener=LISTENER,
        connect_timeout=2.0,
    )


def test_connect_authenticates_with_the_api_key() -> None:
    with ThreadedFakeNatsServer() as server, connect(server) as client:
        assert client is not None
        options = server.connect_options
        assert options is not None
        assert options["user"] == USERNAME
        assert options["pass"] == SECRET
        assert options["name"] == "mxraven-notify"


@pytest.mark.parametrize(
    "overrides",
    [
        {"url": ""},
        {"url": "https://nats.example"},
        {"username": ""},
        {"secret": ""},
        {"listener": ""},
        {"listener": "listener.*"},
        {"connect_timeout": 0.0},
    ],
)
def test_connect_rejects_invalid_options(overrides: dict[str, object]) -> None:
    options: dict[str, object] = {
        "url": "wss://nats.example",
        "username": USERNAME,
        "secret": SECRET,
        "listener": LISTENER,
        **overrides,
    }
    with pytest.raises(ValueError):
        NotificationClient(**options)  # type: ignore[arg-type]


def test_consume_acknowledges_and_stops() -> None:
    with ThreadedFakeNatsServer() as server, connect(server) as client:
        server.enqueue(event("accepted", ACCEPTED_BODY))
        seen: list[NotificationKind] = []

        def handler(notification: NotificationEvent) -> NotificationResult:
            seen.append(notification.kind)
            return STOP_CONSUMING

        client.consume(handler)
        assert seen == [NotificationKind.ACCEPTED]
        server.wait_for_ack("+ACK")


def test_consume_stops_when_closed_from_another_thread() -> None:
    with ThreadedFakeNatsServer() as server, connect(server) as client:
        server.enqueue(event("attempt", SMTP_ATTEMPT_BODY))
        consumed = threading.Event()

        def handler(notification: NotificationEvent) -> NotificationResult:
            consumed.set()
            return None

        worker = threading.Thread(target=client.consume, args=(handler,))
        worker.start()
        server.wait_for_ack("+ACK")
        assert consumed.wait(5.0)
        client.close()
        worker.join(5.0)
        assert not worker.is_alive()


def test_events_yields_for_manual_acknowledgement() -> None:
    with ThreadedFakeNatsServer() as server, connect(server) as client:
        server.enqueue(event("accepted", ACCEPTED_BODY))
        iterator = client.events()
        delivery = next(iterator)
        assert delivery.event.kind is NotificationKind.ACCEPTED
        delivery.ack()
        server.wait_for_ack("+ACK")
        iterator.close()


def test_events_supports_delayed_nak_and_term() -> None:
    with ThreadedFakeNatsServer() as server, connect(server) as client:
        server.enqueue(event("accepted", ACCEPTED_BODY))
        server.enqueue(event("attempt", SMTP_ATTEMPT_BODY))
        iterator = client.events()
        next(iterator).nak(delay=5.0)
        second = next(iterator)
        assert second.event.attempt is not None
        assert second.event.attempt.channel == AttemptChannel.SMTP
        second.term()
        server.wait_for_ack('-NAK {"delay": 5000000000}')
        server.wait_for_ack("+TERM")
        iterator.close()


def test_delivery_settles_only_once() -> None:
    with ThreadedFakeNatsServer() as server, connect(server) as client:
        server.enqueue(event("accepted", ACCEPTED_BODY))
        iterator = client.events()
        delivery = next(iterator)
        delivery.ack()
        with pytest.raises(NotificationError):
            delivery.ack()
        iterator.close()


def test_consume_rejects_after_close() -> None:
    with ThreadedFakeNatsServer() as server:
        client = connect(server)
        client.close()
        with pytest.raises(NotificationError):
            client.consume(lambda _event: None)


def test_close_is_idempotent() -> None:
    with ThreadedFakeNatsServer() as server:
        client = connect(server)
        client.close()
        client.close()
