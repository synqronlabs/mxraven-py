"""MIME body construction.

Builds the serialized body for plain-text, HTML, ``multipart/alternative``, and
``multipart/mixed`` messages. Attachments are always Base64 encoded. Boundaries
are generated randomly and must never be asserted by value in tests.

This module is I/O-free and shared by the synchronous and asynchronous clients.
"""

from __future__ import annotations

import base64
import secrets
from typing import TYPE_CHECKING

from mxraven._internal.address import contains_non_ascii

if TYPE_CHECKING:
    from collections.abc import Iterable

    from mxraven.message import Attachment

__all__ = [
    "build_alternative",
    "encode_base64_lines",
    "encode_rfc2231",
    "ensure_trailing_crlf",
    "format_disposition",
    "new_boundary",
    "normalize_line_endings",
    "wrap_attachments",
]

_CRLF = "\r\n"
_BASE64_LINE = 76
_BOUNDARY_PREFIX = "----=_mxraven_"
_RFC2231_UNRESERVED = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!#$&+-.^_`|~"
)


def new_boundary() -> str:
    """Return a random MIME multipart boundary."""
    return f"{_BOUNDARY_PREFIX}{secrets.token_hex(16)}"


def normalize_line_endings(text: str) -> str:
    """Convert all line endings in `text` to ``CRLF``."""
    text = text.replace("\r\n", "\n")
    text = text.replace("\r", "\n")
    return text.replace("\n", "\r\n")


def ensure_trailing_crlf(text: str) -> str:
    """Append ``CRLF`` to `text` unless it is empty or already terminated."""
    if text and not text.endswith(_CRLF):
        return f"{text}{_CRLF}"
    return text


def encode_base64_lines(data: bytes) -> str:
    """Encode `data` as Base64 wrapped at 76 columns and terminated by ``CRLF``."""
    if not data:
        return ""
    encoded = base64.b64encode(data).decode("ascii")
    lines = [
        encoded[index : index + _BASE64_LINE] for index in range(0, len(encoded), _BASE64_LINE)
    ]
    return _CRLF.join(lines) + _CRLF


def encode_rfc2231(value: str) -> str:
    """Percent-encode a parameter value per RFC 2231."""
    out: list[str] = []
    for char in value:
        if char in _RFC2231_UNRESERVED:
            out.append(char)
        else:
            out.extend(f"%{byte:02X}" for byte in char.encode("utf-8"))
    return "".join(out)


def format_disposition(disposition: str, filename: str) -> str:
    """Render a ``Content-Disposition`` value with an optional filename."""
    if not filename:
        return disposition
    if contains_non_ascii(filename):
        return f"{disposition}; filename*=utf-8''{encode_rfc2231(filename)}"
    escaped = filename.replace("\\", "\\\\").replace('"', '\\"')
    return f'{disposition}; filename="{escaped}"'


def _validate_attachment_field(value: str) -> None:
    if "\r" in value or "\n" in value:
        message = "mail: attachment field must not contain a line break"
        raise ValueError(message)


def build_alternative(text: str, html: str, *, boundary: str) -> tuple[str, str, str]:
    """Render a ``multipart/alternative`` body with text and HTML parts.

    Args:
        text: The plain-text body.
        html: The HTML body.
        boundary: The MIME boundary to use.

    Returns:
        A tuple of the body text, the ``Content-Type`` header value, and the
        ``Content-Transfer-Encoding`` header value.
    """
    encoding = "8bit" if contains_non_ascii(text) or contains_non_ascii(html) else "7bit"
    parts = (
        ("text/plain; charset=utf-8", text),
        ("text/html; charset=utf-8", html),
    )
    out: list[str] = []
    for content_type, part in parts:
        out.append(f"--{boundary}{_CRLF}")
        out.append(f"Content-Type: {content_type}{_CRLF}")
        out.append(f"Content-Transfer-Encoding: {encoding}{_CRLF}")
        out.append(_CRLF)
        out.append(ensure_trailing_crlf(normalize_line_endings(part)))
    out.append(f"--{boundary}--{_CRLF}")
    return "".join(out), f'multipart/alternative; boundary="{boundary}"', encoding


def wrap_attachments(
    body: str,
    body_content_type: str,
    body_encoding: str,
    attachments: Iterable[Attachment],
    *,
    boundary: str,
) -> tuple[str, str, str]:
    """Wrap `body` and attachments in a ``multipart/mixed`` body.

    Args:
        body: The already-rendered body text.
        body_content_type: The body's ``Content-Type``.
        body_encoding: The body's ``Content-Transfer-Encoding``.
        attachments: The attachments to append.
        boundary: The MIME boundary to use.

    Returns:
        A tuple of the body text, the ``Content-Type`` header value, and the
        ``Content-Transfer-Encoding`` header value.
    """
    out: list[str] = [
        f"--{boundary}{_CRLF}",
        f"Content-Type: {body_content_type}{_CRLF}",
        f"Content-Transfer-Encoding: {body_encoding}{_CRLF}",
        _CRLF,
        ensure_trailing_crlf(body),
    ]
    for attachment in attachments:
        _validate_attachment_field(attachment.filename)
        _validate_attachment_field(attachment.content_type)
        _validate_attachment_field(attachment.content_id)
        content_type = attachment.content_type or "application/octet-stream"
        disposition = "inline" if attachment.inline else "attachment"
        out.append(f"--{boundary}{_CRLF}")
        out.append(f"Content-Type: {content_type}{_CRLF}")
        out.append(f"Content-Transfer-Encoding: base64{_CRLF}")
        out.append(
            f"Content-Disposition: {format_disposition(disposition, attachment.filename)}{_CRLF}"
        )
        if attachment.inline and attachment.content_id:
            out.append(f"Content-ID: <{attachment.content_id}>{_CRLF}")
        out.append(_CRLF)
        out.append(encode_base64_lines(attachment.data))
    out.append(f"--{boundary}--{_CRLF}")
    return "".join(out), f'multipart/mixed; boundary="{boundary}"', "7bit"
