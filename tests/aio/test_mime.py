"""Tests for the asynchronous MIME fetch-and-parse."""

from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING

import pytest

from mxraven._internal.http import HttpResponse
from mxraven.aio.mime import fetch_and_parse_raw_email, parse_email
from mxraven.errors import WebhookError
from mxraven.webhook import RawEmail

if TYPE_CHECKING:
    from collections.abc import Mapping

MESSAGE = b"Subject: Inbound\r\nContent-Type: text/plain\r\n\r\nhello"


class AsyncFakeTransport:
    """An asynchronous transport returning a scripted response."""

    def __init__(self, body: bytes = MESSAGE, status: int = 200) -> None:
        self.body = body
        self.status = status

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> HttpResponse:
        del method, url, headers, body, timeout
        return HttpResponse(status=self.status, reason="OK", body=self.body)


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


def test_async_fetch_and_parse() -> None:
    async def scenario() -> None:
        email = await fetch_and_parse_raw_email(raw(), transport=AsyncFakeTransport())
        assert email.subject == "Inbound"
        assert email.text == "hello"

    asyncio.run(scenario())


def test_async_fetch_propagates_digest_failures() -> None:
    async def scenario() -> None:
        with pytest.raises(WebhookError, match="SHA-256"):
            await fetch_and_parse_raw_email(
                raw(sha256_hex="0" * 64), transport=AsyncFakeTransport()
            )

    asyncio.run(scenario())


def test_parse_email_is_shared() -> None:
    assert parse_email(MESSAGE).subject == "Inbound"
