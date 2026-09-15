"""Minimal HTTP transport with injectable synchronous and asynchronous forms.

The default synchronous transport uses :mod:`urllib.request`. The default
asynchronous transport runs the synchronous transport in a worker thread, so
the SDK keeps zero runtime dependencies while still offering an async surface;
callers may inject a native async transport instead.
"""

from __future__ import annotations

import asyncio
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "DEFAULT_ASYNC_TRANSPORT",
    "DEFAULT_SYNC_TRANSPORT",
    "AsyncThreadTransport",
    "AsyncTransport",
    "HttpResponse",
    "SyncTransport",
    "UrllibTransport",
]


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """A minimal HTTP response.

    Attributes:
        status: The HTTP status code.
        reason: The HTTP reason phrase, without the status code.
        body: The response body bytes.
        headers: The response headers keyed by lowercase name.
    """

    status: int
    reason: str
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)


class SyncTransport(Protocol):
    """A synchronous HTTP transport."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> HttpResponse:
        """Perform one HTTP request and return the response."""
        ...


class AsyncTransport(Protocol):
    """An asynchronous HTTP transport."""

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> HttpResponse:
        """Perform one HTTP request and return the response."""
        ...


class UrllibTransport:
    """A synchronous transport backed by :mod:`urllib.request`."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> HttpResponse:
        """Perform one HTTP request with :func:`urllib.request.urlopen`."""
        request = urllib.request.Request(  # noqa: S310 - callers supply http(s) URLs
            url, data=body, headers=dict(headers), method=method
        )
        try:
            with urllib.request.urlopen(  # noqa: S310 - callers supply http(s) URLs
                request, timeout=timeout
            ) as response:
                return _response(response.status, response.reason, response)
        except urllib.error.HTTPError as error:
            return _response(error.code, error.reason, error)


def _response(status: int, reason: str, stream: object) -> HttpResponse:
    body = stream.read()  # type: ignore[attr-defined]
    headers = {
        str(name).lower(): str(value)
        for name, value in stream.headers.items()  # type: ignore[attr-defined]
    }
    return HttpResponse(status=status, reason=reason, body=body, headers=headers)


@dataclass(frozen=True, slots=True)
class AsyncThreadTransport:
    """An asynchronous transport that offloads to a synchronous one."""

    sync: SyncTransport

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> HttpResponse:
        """Run the wrapped synchronous transport in a worker thread."""
        return await asyncio.to_thread(
            self.sync.request,
            method,
            url,
            headers=headers,
            body=body,
            timeout=timeout,
        )


DEFAULT_SYNC_TRANSPORT: SyncTransport = UrllibTransport()
DEFAULT_ASYNC_TRANSPORT: AsyncTransport = AsyncThreadTransport(DEFAULT_SYNC_TRANSPORT)
