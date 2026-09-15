"""Tests for raw-email download and verification."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from mxraven._internal.http import HttpResponse
from mxraven.errors import WebhookError
from mxraven.webhook import RawEmail

if TYPE_CHECKING:
    from collections.abc import Mapping

BODY = b"From: sender@example.net\r\nSubject: Hello\r\n\r\nbody\r\n"


@dataclass
class FakeTransport:
    """A transport returning a scripted response."""

    status: int = 200
    reason: str = "OK"
    body: bytes = BODY
    requests: list[tuple[str, str, Mapping[str, str]]] = field(default_factory=list)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> HttpResponse:
        del body, timeout
        self.requests.append((method, url, headers))
        return HttpResponse(status=self.status, reason=self.reason, body=self.body)


def raw(**overrides: object) -> RawEmail:
    defaults: dict[str, object] = {
        "url": "https://raw.example.com/messages/task-123.eml",
        "token_type": "Bearer",
        "access_token": "short-lived-token",
        "size_bytes": len(BODY),
        "sha256_hex": hashlib.sha256(BODY).hexdigest(),
    }
    defaults.update(overrides)
    return RawEmail(**defaults)  # type: ignore[arg-type]


def test_fetch_verifies_and_sends_bearer() -> None:
    transport = FakeTransport()
    assert raw().fetch(transport=transport) == BODY
    method, _url, headers = transport.requests[0]
    assert method == "GET"
    assert headers["Authorization"] == "Bearer short-lived-token"


def test_fetch_defaults_token_type_to_bearer() -> None:
    transport = FakeTransport()
    raw(token_type="").fetch(transport=transport)
    assert transport.requests[0][2]["Authorization"] == "Bearer short-lived-token"


def test_fetch_rejects_size_mismatch() -> None:
    with pytest.raises(WebhookError, match="size"):
        raw(size_bytes=len(BODY) + 1).fetch(transport=FakeTransport())


def test_fetch_rejects_oversized_body() -> None:
    with pytest.raises(WebhookError, match="exceeds"):
        raw(size_bytes=5).fetch(transport=FakeTransport())


def test_fetch_async() -> None:
    @dataclass
    class AsyncFake:
        async def request(self, method, url, *, headers, body, timeout) -> HttpResponse:
            del method, url, headers, body, timeout
            return HttpResponse(status=200, reason="OK", body=BODY)

    assert asyncio.run(raw().fetch_async(transport=AsyncFake())) == BODY


def test_fetch_rejects_digest_mismatch() -> None:
    with pytest.raises(WebhookError, match="SHA-256"):
        raw(sha256_hex="0" * 64).fetch(transport=FakeTransport())


def test_fetch_accepts_any_2xx() -> None:
    assert raw().fetch(transport=FakeTransport(status=206, reason="Partial Content")) == BODY


def test_fetch_rejects_non_success() -> None:
    with pytest.raises(WebhookError, match="401"):
        raw().fetch(transport=FakeTransport(status=401, reason="Unauthorized", body=b""))


def test_fetch_requires_url_and_token() -> None:
    with pytest.raises(WebhookError, match="URL"):
        raw(url="").fetch(transport=FakeTransport())
    with pytest.raises(WebhookError, match="token"):
        raw(access_token="").fetch(transport=FakeTransport())
