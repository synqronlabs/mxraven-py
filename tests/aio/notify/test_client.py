"""Asynchronous notification client tests against an in-process fake NATS server."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import pytest

from mxraven.aio.notify import NotificationClient
from mxraven.errors import DecodeError, NotificationError
from mxraven.notify import (
    STOP_CONSUMING,
    AttemptChannel,
    NotificationEvent,
    NotificationKind,
    NotificationResult,
)
from tests.notify.fake_nats import FakeNatsEvent, FakeNatsServer

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

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


@asynccontextmanager
async def running_server(**kwargs: object) -> AsyncIterator[FakeNatsServer]:
    server = await FakeNatsServer(**kwargs).start()  # type: ignore[arg-type]
    try:
        yield server
    finally:
        await server.close()


@asynccontextmanager
async def connected(
    server: FakeNatsServer, **overrides: object
) -> AsyncIterator[NotificationClient]:
    client = await NotificationClient.connect(
        url=server.url,
        username=USERNAME,
        secret=SECRET,
        listener=LISTENER,
        connect_timeout=2.0,
        **overrides,  # type: ignore[arg-type]
    )
    try:
        yield client
    finally:
        await client.close()


def test_connect_authenticates_with_the_api_key() -> None:
    async def scenario() -> None:
        async with running_server() as server, connected(server):
            options = server.connect_options
            assert options is not None
            assert options["user"] == USERNAME
            assert options["pass"] == SECRET
            assert options["name"] == "mxraven-notify"

    asyncio.run(scenario())


def test_connect_rejects_a_missing_consumer() -> None:
    async def scenario() -> None:
        async with running_server(consumer_not_found=True) as server:
            with pytest.raises(NotificationError):
                await NotificationClient.connect(
                    url=server.url,
                    username=USERNAME,
                    secret=SECRET,
                    listener=LISTENER,
                    connect_timeout=2.0,
                )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "overrides",
    [
        {"url": ""},
        {"url": "https://nats.example"},
        {"username": ""},
        {"secret": ""},
        {"listener": ""},
        {"listener": "listener.*"},
        {"listener": "listener/1"},
        {"consumer": "notify.listener"},
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


def test_consumes_and_acknowledges_an_accepted_event() -> None:
    async def scenario() -> None:
        async with running_server() as server:
            server.enqueue(event("accepted", ACCEPTED_BODY))
            async with connected(server) as client:
                seen: list[NotificationKind] = []

                def record(notification: NotificationEvent) -> NotificationResult:
                    seen.append(notification.kind)
                    return STOP_CONSUMING

                await client.consume(record)
                assert seen == [NotificationKind.ACCEPTED]
                await server.wait_for_ack("+ACK")

    asyncio.run(scenario())


def test_redelivers_a_failed_handler_with_backoff() -> None:
    async def scenario() -> None:
        async with running_server() as server:
            server.enqueue(event("attempt", SMTP_ATTEMPT_BODY))
            async with connected(server) as client:

                def fail(_notification: NotificationEvent) -> NotificationResult:
                    message = "transient failure"
                    raise RuntimeError(message)

                task = asyncio.create_task(client.consume(fail))
                await server.wait_for_ack('-NAK {"delay": 1000000000}')
                await client.close()
                await asyncio.wait_for(task, 5.0)

    asyncio.run(scenario())


def test_terminates_undecodable_event_and_raises() -> None:
    async def scenario() -> None:
        async with running_server() as server:
            server.enqueue(event("accepted", b"{"))
            async with connected(server) as client:
                with pytest.raises(DecodeError):
                    await client.consume(_noop)
                await server.wait_for_ack("+TERM")

    asyncio.run(scenario())


def test_reports_undecodable_event_and_continues_with_on_error() -> None:
    async def scenario() -> None:
        async with running_server() as server:
            server.enqueue(event("accepted", b"{"))
            server.enqueue(event("attempt", SMTP_ATTEMPT_BODY))
            reported: list[NotificationError] = []
            async with connected(server, on_error=reported.append) as client:
                handled: list[NotificationKind] = []
                done = asyncio.Event()

                def handle(notification: NotificationEvent) -> NotificationResult:
                    handled.append(notification.kind)
                    done.set()
                    return None

                task = asyncio.create_task(client.consume(handle))
                await asyncio.wait_for(done.wait(), 5.0)
                assert handled == [NotificationKind.ATTEMPT]
                assert isinstance(reported[0], DecodeError)
                await server.wait_for_ack("+TERM")
                await client.close()
                await asyncio.wait_for(task, 5.0)

    asyncio.run(scenario())


def test_consume_stops_when_the_client_closes() -> None:
    async def scenario() -> None:
        async with running_server() as server, connected(server) as client:
            task = asyncio.create_task(client.consume(_noop))
            await asyncio.sleep(0.05)
            await client.close()
            await asyncio.wait_for(task, 5.0)

    asyncio.run(scenario())


def test_consume_rejects_after_close() -> None:
    async def scenario() -> None:
        async with running_server() as server, connected(server) as client:
            await client.close()
            with pytest.raises(NotificationError):
                await client.consume(_noop)

    asyncio.run(scenario())


def test_events_yields_for_manual_acknowledgement() -> None:
    async def scenario() -> None:
        async with running_server() as server:
            server.enqueue(event("accepted", ACCEPTED_BODY))
            async with connected(server) as client:
                iterator = client.events()
                delivery = await asyncio.wait_for(iterator.__anext__(), 5.0)
                assert delivery.event.kind is NotificationKind.ACCEPTED
                assert delivery.event.attempt is None
                await delivery.ack()
                await server.wait_for_ack("+ACK")
                await iterator.aclose()

    asyncio.run(scenario())


def test_events_supports_delayed_nak_and_term() -> None:
    async def scenario() -> None:
        async with running_server() as server:
            server.enqueue(event("accepted", ACCEPTED_BODY))
            server.enqueue(event("attempt", SMTP_ATTEMPT_BODY))
            async with connected(server) as client:
                iterator = client.events()
                first = await asyncio.wait_for(iterator.__anext__(), 5.0)
                await first.nak(delay=5.0)
                second = await asyncio.wait_for(iterator.__anext__(), 5.0)
                assert second.event.attempt is not None
                assert second.event.attempt.channel == AttemptChannel.SMTP
                await second.term()
                await server.wait_for_ack('-NAK {"delay": 5000000000}')
                await server.wait_for_ack("+TERM")
                await iterator.aclose()

    asyncio.run(scenario())


def test_events_rejects_an_undecodable_event() -> None:
    async def scenario() -> None:
        async with running_server() as server:
            server.enqueue(event("accepted", b"{"))
            async with connected(server) as client:
                iterator = client.events()
                with pytest.raises(DecodeError):
                    await iterator.__anext__()
                await iterator.aclose()

    asyncio.run(scenario())


def test_events_rejects_when_used_after_close() -> None:
    async def scenario() -> None:
        async with running_server() as server, connected(server) as client:
            await client.close()
            with pytest.raises(NotificationError):
                await client.events().__anext__()

    asyncio.run(scenario())


def test_close_is_idempotent() -> None:
    async def scenario() -> None:
        async with running_server() as server, connected(server) as client:
            await client.close()
            await client.close()

    asyncio.run(scenario())


def test_iterator_close_is_idempotent() -> None:
    async def scenario() -> None:
        async with running_server() as server, connected(server) as client:
            iterator = client.events()
            await iterator.aclose()
            await iterator.aclose()

    asyncio.run(scenario())


def test_delivery_settles_only_once() -> None:
    async def scenario() -> None:
        async with running_server() as server:
            server.enqueue(event("accepted", ACCEPTED_BODY))
            async with connected(server) as client:
                iterator = client.events()
                delivery = await asyncio.wait_for(iterator.__anext__(), 5.0)
                await delivery.ack()
                with pytest.raises(NotificationError):
                    await delivery.ack()
                await iterator.aclose()

    asyncio.run(scenario())


def _noop(_notification: NotificationEvent) -> NotificationResult:
    return None
