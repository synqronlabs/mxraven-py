"""Framework-neutral webhook request representation and adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["WebhookRequest"]


@dataclass(frozen=True, slots=True)
class WebhookRequest:
    """The parts of an inbound HTTP request the signature covers.

    Header names are stored lowercased so lookups are case-insensitive.

    Attributes:
        method: The HTTP method.
        url: The public URL the caller used, including scheme and host.
        headers: The request headers keyed by lowercase name.
        body: The exact raw request body bytes.
    """

    method: str
    url: str
    headers: Mapping[str, str]
    body: bytes = b""

    @classmethod
    def create(
        cls,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | bytearray | memoryview = b"",
    ) -> WebhookRequest:
        """Build a request, normalizing header names and body type."""
        normalized = {str(name).lower(): str(value) for name, value in headers.items()}
        return cls(method=method.upper(), url=url, headers=normalized, body=bytes(body))

    def header(self, name: str) -> str:
        """Return a header value, or an empty string when absent."""
        return self.headers.get(name.lower(), "")

    @classmethod
    def from_wsgi(cls, environ: Mapping[str, object]) -> WebhookRequest:
        """Build a request from a WSGI ``environ``.

        Note:
            The signature covers the exact raw body and the public URL, so read
            the body here rather than letting a framework parse it first.
        """
        method = str(environ.get("REQUEST_METHOD", ""))
        scheme = str(environ.get("wsgi.url_scheme", "https"))
        host = str(environ.get("HTTP_HOST") or environ.get("SERVER_NAME") or "")
        path = str(environ.get("PATH_INFO", ""))
        query = str(environ.get("QUERY_STRING", ""))
        url = f"{scheme}://{host}{path}" + (f"?{query}" if query else "")
        headers = {
            str(key)[5:].replace("_", "-").lower(): str(value)
            for key, value in environ.items()
            if str(key).startswith("HTTP_")
        }
        content_type = environ.get("CONTENT_TYPE")
        if isinstance(content_type, str) and content_type:
            headers["content-type"] = content_type
        length_value = environ.get("CONTENT_LENGTH")
        length = int(str(length_value)) if length_value else 0
        stream = environ.get("wsgi.input")
        body = stream.read(length) if length and hasattr(stream, "read") else b""
        return cls.create(method, url, headers, body)

    @classmethod
    def from_asgi(cls, scope: Mapping[str, object], body: bytes) -> WebhookRequest:
        """Build a request from an ASGI ``scope`` and pre-read body."""
        method = str(scope.get("method", ""))
        scheme = str(scope.get("scheme", "https"))
        raw_headers = scope.get("headers")
        headers: dict[str, str] = {}
        if isinstance(raw_headers, list):
            for name, value in raw_headers:
                headers[bytes(name).decode("latin-1").lower()] = bytes(value).decode("latin-1")
        host = headers.get("host", "")
        path = str(scope.get("path", "/"))
        query_bytes = scope.get("query_string", b"")
        query = bytes(query_bytes).decode("latin-1") if isinstance(query_bytes, bytes) else ""
        url = f"{scheme}://{host}{path}" + (f"?{query}" if query else "")
        return cls.create(method, url, headers, body)
