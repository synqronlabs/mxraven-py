"""Tests for the feedback client, ported from the Go SDK."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from mxraven._internal.http import HttpResponse
from mxraven.errors import FeedbackError
from mxraven.feedback import AsyncClient, Client, Disposition, LearningResult

if TYPE_CHECKING:
    from collections.abc import Mapping

RAW = b"From: a@example.com\r\nSubject: sample\r\n\r\nbody\r\n"
SUCCESS_BODY = (
    b'{"status":"learned","disposition":"spam","tenant_id":"t1",'
    b'"listener_id":"l1","matched_hash_kind":"rendered_eml_sha256"}'
)


@dataclass
class FakeTransport:
    """A synchronous transport returning a scripted response."""

    status: int = 200
    reason: str = "OK"
    body: bytes = SUCCESS_BODY
    requests: list[tuple[str, str, Mapping[str, str], bytes | None]] = field(default_factory=list)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> HttpResponse:
        del timeout
        self.requests.append((method, url, headers, body))
        return HttpResponse(status=self.status, reason=self.reason, body=self.body)


@dataclass
class FakeAsyncTransport:
    """An asynchronous transport returning a scripted response."""

    status: int = 200
    reason: str = "OK"
    body: bytes = SUCCESS_BODY
    requests: list[tuple[str, str, Mapping[str, str], bytes | None]] = field(default_factory=list)

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> HttpResponse:
        del timeout
        self.requests.append((method, url, headers, body))
        return HttpResponse(status=self.status, reason=self.reason, body=self.body)


def make_client(transport: FakeTransport, **overrides: object) -> Client:
    options: dict[str, object] = {
        "base_url": "https://feedback.mxraven.com",
        "username": "mxr_tx_ab12cd34ef56",
        "secret": "supersecret",
    }
    options.update(overrides)
    return Client(transport=transport, **options)  # type: ignore[arg-type]


def test_learn_spam() -> None:
    transport = FakeTransport()
    result = make_client(transport).learn_spam(RAW)
    method, url, headers, body = transport.requests[0]
    assert method == "POST"
    assert url == "https://feedback.mxraven.com/v1/feedback/learn/spam"
    assert headers["Content-Type"] == "message/rfc822"
    expected = base64.b64encode(b"mxr_tx_ab12cd34ef56:supersecret").decode()
    assert headers["Authorization"] == f"Basic {expected}"
    assert body == RAW
    assert result == LearningResult("learned", "spam", "t1", "l1", "rendered_eml_sha256")


def test_learn_ham_path() -> None:
    transport = FakeTransport()
    make_client(transport).learn(Disposition.HAM, RAW)
    assert transport.requests[0][1].endswith("/v1/feedback/learn/ham")


def test_learn_requires_credentials() -> None:
    with pytest.raises(ValueError, match="credentials"):
        make_client(FakeTransport(), username="", secret="").learn_spam(RAW)


def test_learn_error_mapping() -> None:
    transport = FakeTransport(
        status=404, reason="Not Found", body=b'{"error":"evidence not found"}'
    )
    with pytest.raises(FeedbackError) as info:
        make_client(transport).learn_spam(RAW)
    assert info.value.status_code == 404
    assert info.value.message == "evidence not found"
    assert info.value.retryable is False


def test_learn_retryable() -> None:
    transport = FakeTransport(status=429, reason="Too Many Requests", body=b"{}")
    with pytest.raises(FeedbackError) as info:
        make_client(transport).learn_spam(RAW)
    assert info.value.retryable is True


def test_accepts_any_2xx() -> None:
    assert make_client(FakeTransport(status=201)).learn_spam(RAW).status == "learned"
    make_client(FakeTransport(status=204, body=b"")).unsubscribe("a.b.c")


def test_unsubscribe() -> None:
    transport = FakeTransport(body=b"")
    make_client(transport).unsubscribe("header.payload.signature")
    method, url, headers, body = transport.requests[0]
    assert method == "POST"
    assert url == "https://feedback.mxraven.com/v1/feedback/unsubscribe/header.payload.signature"
    assert headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert "Authorization" not in headers
    assert body == b"List-Unsubscribe=One-Click"


def test_unsubscribe_requires_token() -> None:
    with pytest.raises(ValueError, match="token"):
        make_client(FakeTransport()).unsubscribe("   ")


@pytest.mark.parametrize("base_url", ["", "   "])
def test_invalid_base_url(base_url: str) -> None:
    with pytest.raises(ValueError, match="base URL"):
        Client(base_url=base_url)


@pytest.mark.parametrize(
    ("kwargs", "pattern"),
    [
        ({"username": "u"}, "both"),
        ({"secret": "s"}, "both"),
        ({"timeout": 0}, "timeout"),
    ],
)
def test_invalid_options(kwargs: dict[str, object], pattern: str) -> None:
    with pytest.raises(ValueError, match=pattern):
        Client(base_url="https://feedback.example.com", **kwargs)  # type: ignore[arg-type]


def test_accepts_base_url_without_credentials() -> None:
    Client(base_url="https://feedback.example.com")


def test_invalid_disposition() -> None:
    client = make_client(FakeTransport())
    with pytest.raises(ValueError, match="invalid disposition"):
        client.learn("bogus", RAW)  # type: ignore[arg-type]


def test_transport_failure_is_wrapped() -> None:
    class FailingTransport:
        def request(self, method: str, url: str, **kwargs: object) -> HttpResponse:
            del method, url, kwargs
            error = "network down"
            raise OSError(error)

    client = Client(
        base_url="https://feedback.example.com",
        username="u",
        secret="s",
        transport=FailingTransport(),
    )
    with pytest.raises(FeedbackError, match="submit learning request"):
        client.learn_spam(RAW)


def test_error_detail_alias() -> None:
    error = FeedbackError(404, "not found")
    assert error.detail == "not found"


def test_unsubscribe_with_trailing_slash_base_url() -> None:
    transport = FakeTransport(body=b"")
    client = Client(base_url="https://feedback.example.com/", transport=transport)
    client.unsubscribe("header.payload.signature")
    assert transport.requests[0][1] == (
        "https://feedback.example.com/v1/feedback/unsubscribe/header.payload.signature"
    )


def test_async_learn_and_unsubscribe() -> None:
    transport = FakeAsyncTransport(body=b"")

    async def run() -> None:
        client = AsyncClient(
            "https://feedback.mxraven.com",
            username="u",
            secret="s",
            transport=transport,
        )
        await client.unsubscribe("a.b.c")

    asyncio.run(run())
    assert transport.requests[0][1].endswith("/v1/feedback/unsubscribe/a.b.c")
