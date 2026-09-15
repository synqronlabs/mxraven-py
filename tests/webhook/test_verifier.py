"""Tests for webhook signature verification, ported from the Go SDK."""

from __future__ import annotations

import hashlib
import hmac
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import pytest

from mxraven.errors import InvalidSignatureError, WebhookError
from mxraven.webhook import EventType, Verifier, WebhookRequest

if TYPE_CHECKING:
    from collections.abc import Mapping

SECRET = "whsec_dGVzdHNlY3JldA"
OTHER_SECRET = "whsec_other"
KID = "whk_test"
URL = "https://hooks.example.com/mxraven?tenant=acme"
WEBHOOK_ID = "task-123"

INBOUND_BODY = (
    "{\n"
    '\t"event_type": "inbound_email",\n'
    '\t"task_id": "task-123",\n'
    '\t"tenant_id": "tenant-1",\n'
    '\t"listener_id": "listener-1",\n'
    '\t"attempt": 1,\n'
    '\t"accepted_at_utc": 1789302600,\n'
    '\t"occurred_at_utc": 1789302601,\n'
    '\t"routing_decision": {"terminal_action": "TERMINAL_ACTION_TYPE_RELAY", '
    '"used_listener_default": false},\n'
    '\t"envelope": {"mail_from": "sender@example.net", "rcpt_to": ["support@example.com"]},\n'
    '\t"message": {"subject": "Hello"},\n'
    '\t"headers": [{"name": "From", "value": "sender@example.net"}],\n'
    '\t"raw_email": {"url": "https://raw.example.com/messages/task-123.eml", '
    '"token_type": "Bearer", "access_token": "token"}\n'
    "}"
)


def sign(secret: str, timestamp: str, body: str, *, method: str = "POST", url: str = URL) -> str:
    parts = urlsplit(url)
    target = parts.path if parts.path else "/"
    if parts.query:
        target = f"{target}?{parts.query}"
    canonical = "\n".join(
        [
            timestamp,
            WEBHOOK_ID,
            method,
            parts.netloc.lower(),
            target,
            hashlib.sha256(body.encode()).hexdigest(),
        ]
    )
    digest = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def make_request(
    body: str = INBOUND_BODY,
    *,
    timestamp: str = "1700000000",
    secret: str = SECRET,
    url: str = URL,
    signature: str | None = None,
    **extra: str,
) -> WebhookRequest:
    headers: Mapping[str, str] = {
        "X-MxRaven-Webhook-ID": WEBHOOK_ID,
        "X-MxRaven-Timestamp": timestamp,
        "X-MxRaven-Signature-Kid": KID,
        "X-MxRaven-Signature": signature
        if signature is not None
        else sign(secret, timestamp, body, url=url),
        **extra,
    }
    return WebhookRequest.create("POST", url, headers, body.encode())


def fixed_clock(value: float):
    return lambda: value


def test_inbound_body_hash_matches_reference() -> None:
    assert len(INBOUND_BODY.encode()) == 601
    assert (
        hashlib.sha256(INBOUND_BODY.encode()).hexdigest()
        == "bc19c3af7e20b2b0220947688a9b7bafab50f3287706fde8d1949c1a4d58adcf"
    )


@pytest.mark.parametrize(
    ("secret", "timestamp", "expected"),
    [
        (SECRET, "1700000000", "0e5649769319ed903f0b35e94bea23b47565d08b9ba837b84a8e44c2a3712d5c"),
        (SECRET, "1789302600", "071c5fa0dcc99f45efab28d1841860dfcd8a836dbe1ae6bd2c1e1ccc74d8b964"),
        (
            OTHER_SECRET,
            "1700000000",
            "3cece8bde5e485e4b86889c88fe2365dce6bd5ca1ad97e5d416a55809a028a44",
        ),
    ],
)
def test_signature_vectors(secret: str, timestamp: str, expected: str) -> None:
    assert sign(secret, timestamp, INBOUND_BODY) == f"sha256={expected}"


def test_verify_and_decode() -> None:
    verifier = Verifier(secret=SECRET, clock=fixed_clock(1700000000))
    event = verifier.verify_and_decode(make_request())
    assert event.type is EventType.INBOUND_EMAIL
    assert event.inbound_email is not None
    assert event.inbound_email.task_id == "task-123"
    assert event.inbound_email.routing_decision is not None
    assert event.inbound_email.routing_decision.terminal_action == "TERMINAL_ACTION_TYPE_RELAY"


def test_rejects_tampered_body() -> None:
    request = make_request()
    tampered = WebhookRequest.create(
        request.method, request.url, request.headers, request.body + b" "
    )
    with pytest.raises(InvalidSignatureError):
        Verifier(secret=SECRET, clock=fixed_clock(1700000000)).verify(tampered)


def test_rejects_wrong_secret() -> None:
    request = make_request(secret=OTHER_SECRET)
    with pytest.raises(InvalidSignatureError):
        Verifier(secret=SECRET, clock=fixed_clock(1700000000)).verify(request)


@pytest.mark.parametrize(
    "header", ["X-MxRaven-Webhook-ID", "X-MxRaven-Timestamp", "X-MxRaven-Signature"]
)
def test_missing_headers(header: str) -> None:
    request = make_request()
    headers = {
        name: value for name, value in request.headers.items() if name.lower() != header.lower()
    }
    stripped = WebhookRequest.create(request.method, request.url, headers, request.body)
    with pytest.raises(WebhookError):
        Verifier(secret=SECRET, clock=fixed_clock(1700000000)).verify(stripped)


def test_tolerance() -> None:
    request = make_request(timestamp="1699999400")
    with pytest.raises(WebhookError, match="clock skew"):
        Verifier(secret=SECRET, clock=fixed_clock(1700000000)).verify(request)
    Verifier(secret=SECRET, tolerance=0, clock=fixed_clock(1700000000)).verify(request)


def test_key_rotation() -> None:
    request = make_request()
    Verifier(keys={KID: SECRET}, clock=fixed_clock(1700000000)).verify(request)
    with pytest.raises(WebhookError, match="unknown signature key ID"):
        Verifier(keys={"whk_other": SECRET}, clock=fixed_clock(1700000000)).verify(request)


def test_key_rotation_requires_kid() -> None:
    request = make_request()
    headers = {
        name: value
        for name, value in request.headers.items()
        if name.lower() != "x-mxraven-signature-kid"
    }
    without_kid = WebhookRequest.create(request.method, request.url, headers, request.body)
    with pytest.raises(WebhookError, match="missing signature key ID"):
        Verifier(keys={KID: SECRET}, clock=fixed_clock(1700000000)).verify(without_kid)


def test_unsupported_scheme() -> None:
    request = make_request(signature="sha1=deadbeef")
    with pytest.raises(WebhookError, match="unsupported signature algorithm"):
        Verifier(secret=SECRET, clock=fixed_clock(1700000000)).verify(request)


def test_request_target_is_signed() -> None:
    request = make_request()
    moved = WebhookRequest.create(
        request.method, "https://hooks.example.com/different", request.headers, request.body
    )
    with pytest.raises(InvalidSignatureError):
        Verifier(secret=SECRET, clock=fixed_clock(1700000000)).verify(moved)


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"secret": ""},
        {"keys": {"": SECRET}},
        {"keys": {KID: ""}},
        {"secret": SECRET, "tolerance": -1},
        {"secret": SECRET, "max_body_bytes": 0},
    ],
)
def test_invalid_options(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        Verifier(**kwargs)  # type: ignore[arg-type]
