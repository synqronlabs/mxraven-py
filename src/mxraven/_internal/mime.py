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
    "encode_body",
    "encode_quoted_printable",
    "encode_rfc2231",
    "ensure_trailing_crlf",
    "exceeds_line_length",
    "format_disposition",
    "new_boundary",
    "normalize_line_endings",
    "wrap_attachments",
]

_CRLF = "\r\n"
_BASE64_LINE = 76
_QP_LINE_LENGTH = 76
_MAX_LINE_LENGTH = 998
_TAB = 0x09
_SPACE = 0x20
_EQUALS = 0x3D
_PRINTABLE_MAX = 0x7E
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


def exceeds_line_length(text: str, *, limit: int = _MAX_LINE_LENGTH) -> bool:
    """Report whether any line exceeds the RFC 5322 octet limit.

    Args:
        text: The text to inspect. Line endings may be ``LF`` or ``CRLF``.
        limit: The maximum number of octets per line, excluding the line
            ending. Defaults to the RFC 5322 maximum of ``998``.

    Returns:
        `True` when at least one line encodes to more than `limit` UTF-8
        octets.
    """
    normalized = normalize_line_endings(text)
    return any(len(line.encode("utf-8")) > limit for line in normalized.split(_CRLF))


def encode_quoted_printable(text: str) -> str:
    """Encode text as quoted-printable per RFC 2045.

    Literal ``CRLF`` sequences are preserved as line breaks. Each output line
    is at most 76 characters: longer content is split with ``=`` soft line
    breaks, and any trailing space or tab is encoded so it survives transport.
    The result is pure ASCII and does not add a trailing line ending.

    Args:
        text: The text to encode. It must already use ``CRLF`` line endings.

    Returns:
        The quoted-printable text, without a trailing line ending.
    """
    if not text:
        return ""
    return _CRLF.join(_encode_qp_line(line) for line in text.split(_CRLF))


def _encode_qp_line(line: str) -> str:
    data = line.encode("utf-8")
    trailing_start = len(data)
    while trailing_start > 0 and data[trailing_start - 1] in (_SPACE, _TAB):
        trailing_start -= 1
    tokens: list[str] = []
    for index, byte in enumerate(data):
        trailing_whitespace = index >= trailing_start and byte in (_SPACE, _TAB)
        if trailing_whitespace or byte == _EQUALS:
            tokens.append(f"={byte:02X}")
        elif _SPACE <= byte <= _PRINTABLE_MAX or byte == _TAB:
            tokens.append(chr(byte))
        else:
            tokens.append(f"={byte:02X}")
    return _soft_wrap(tokens)


def _soft_wrap(tokens: list[str]) -> str:
    lines: list[str] = []
    current: list[str] = []
    current_length = 0
    for token in tokens:
        if current_length + len(token) > _QP_LINE_LENGTH - 1:
            lines.append("".join(current) + "=")
            current = []
            current_length = 0
        current.append(token)
        current_length += len(token)
    lines.append("".join(current))
    return _CRLF.join(lines)


def encode_body(text: str) -> tuple[str, str]:
    """Encode a text body and select its transfer encoding.

    Quoted-printable is used when any line would exceed the RFC 5322 limit so
    the serialized message honors the line-length rule. Otherwise ``8bit`` is
    used for non-ASCII text and ``7bit`` for ASCII text.

    Args:
        text: The raw body text, in any line-ending convention.

    Returns:
        A tuple of the encoded body with ``CRLF`` line endings and a trailing
        line ending, and the ``Content-Transfer-Encoding`` value.
    """
    normalized = ensure_trailing_crlf(normalize_line_endings(text))
    if exceeds_line_length(normalized):
        return ensure_trailing_crlf(encode_quoted_printable(normalized)), "quoted-printable"
    if contains_non_ascii(normalized):
        return normalized, "8bit"
    return normalized, "7bit"


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
    text_body, text_encoding = encode_body(text)
    html_body, html_encoding = encode_body(html)
    parts = (
        ("text/plain; charset=utf-8", text_body, text_encoding),
        ("text/html; charset=utf-8", html_body, html_encoding),
    )
    out: list[str] = []
    for content_type, part_body, part_encoding in parts:
        out.append(f"--{boundary}{_CRLF}")
        out.append(f"Content-Type: {content_type}{_CRLF}")
        out.append(f"Content-Transfer-Encoding: {part_encoding}{_CRLF}")
        out.append(_CRLF)
        out.append(part_body)
    out.append(f"--{boundary}--{_CRLF}")
    encoding = "8bit" if "8bit" in (text_encoding, html_encoding) else "7bit"
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
