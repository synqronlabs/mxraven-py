"""Tests for download-and-parse, ported from mxraven-js fetch-and-parse.test.ts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from mxraven._internal.http import HttpResponse
from mxraven.errors import WebhookError
from mxraven.mime import fetch_and_parse_raw_email
from mxraven.webhook import RawEmail

if TYPE_CHECKING:
    from collections.abc import Mapping

MESSAGE = b"Subject: Inbound\r\nContent-Type: text/plain\r\n\r\nhello"


@dataclass
class FakeTransport:
    """A transport returning a scripted response."""

    status: int = 200
    reason: str = "OK"
    body: bytes = MESSAGE
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
        "url": "https://raw.example.com/message.eml",
        "token_type": "Bearer",
        "access_token": "token",
        "size_bytes": len(MESSAGE),
        "sha256_hex": hashlib.sha256(MESSAGE).hexdigest(),
    }
    defaults.update(overrides)
    return RawEmail(**defaults)  # type: ignore[arg-type]


def test_downloads_verifies_and_parses() -> None:
    transport = FakeTransport()
    email = fetch_and_parse_raw_email(raw(), transport=transport)
    assert email.subject == "Inbound"
    assert email.text == "hello"
    assert len(transport.requests) == 1
    method, url, headers = transport.requests[0]
    assert method == "GET"
    assert url == "https://raw.example.com/message.eml"
    assert headers["Authorization"] == "Bearer token"


def test_propagates_digest_failures() -> None:
    with pytest.raises(WebhookError, match="SHA-256"):
        fetch_and_parse_raw_email(raw(sha256_hex="0" * 64), transport=FakeTransport())
