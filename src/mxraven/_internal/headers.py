"""RFC 5322 header serialization and validation.

Header values are folded to stay within the recommended 78-column limit where
possible, and never exceed the 998-octet hard limit. The folder is deliberately
simple: values are split on runs of spaces and tabs and greedily re-wrapped, so
the exact fold points may differ from the Go builder while the parsed result is
equivalent.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "fold_header",
    "serialize_headers",
    "validate_header",
    "validate_header_name",
    "validate_header_value",
]

_RECOMMENDED_LIMIT = 78
_HARD_LIMIT = 998
_CONTROL_MAX = 0x20
_DELETE = 0x7F
_NAME_MIN = 0x21
_NAME_MAX = 0x7E
_WHITESPACE_RUN = re.compile(r"[ \t]+")
_CRLF = "\r\n"


def validate_header_name(name: str) -> None:
    """Validate an RFC 5322 header field name.

    Args:
        name: The header name.

    Raises:
        ValueError: The name is empty or contains a character outside the
            printable ASCII range, or a colon.
    """
    if name == "":
        message = "mail: header name must not be empty"
        raise ValueError(message)
    for char in name:
        code = ord(char)
        if code < _NAME_MIN or code > _NAME_MAX or char == ":":
            message = f"mail: invalid header name: {name!r}"
            raise ValueError(message)


def validate_header_value(value: str) -> None:
    """Validate an RFC 5322 header field value.

    Args:
        value: The header value.

    Raises:
        ValueError: The value contains a line break or an invalid control
            character.
    """
    if "\r" in value or "\n" in value:
        message = "mail: header value must not contain a line break"
        raise ValueError(message)
    for char in value:
        code = ord(char)
        if (code < _CONTROL_MAX and char != "\t") or code == _DELETE:
            message = "mail: header value contains an invalid control character"
            raise ValueError(message)


def validate_header(name: str, value: str) -> None:
    """Validate a header name and value.

    Raises:
        ValueError: The name or value is invalid.
    """
    validate_header_name(name)
    validate_header_value(value)


def fold_header(name: str, value: str) -> str:
    """Serialize one header, folding its value when necessary.

    Args:
        name: The header name.
        value: The header value; it must not contain a line break.

    Returns:
        The header line(s), each terminated with ``CRLF``.

    Raises:
        ValueError: The name or value is invalid, or a single word is too long
            to represent within the 998-octet limit.
    """
    validate_header_name(name)
    validate_header_value(value)
    prefix = f"{name}: "
    if len(prefix) + len(value) <= _RECOMMENDED_LIMIT:
        return f"{prefix}{value}{_CRLF}"
    words = [word for word in _WHITESPACE_RUN.split(value) if word]
    for word in words:
        if len(word) + 1 > _HARD_LIMIT:
            message = f"mail: header {name!r} contains a word too long to fold"
            raise ValueError(message)
    if not words:
        return f"{prefix}{_CRLF}"
    lines: list[str] = []
    current = prefix
    for word in words:
        if current == prefix:
            current = current + word
        else:
            candidate = f"{current} {word}"
            if len(candidate) <= _RECOMMENDED_LIMIT:
                current = candidate
            else:
                lines.append(f"{current}{_CRLF}")
                current = f" {word}"
    lines.append(f"{current}{_CRLF}")
    return "".join(lines)


def serialize_headers(headers: Iterable[tuple[str, str]]) -> bytes:
    """Serialize headers to bytes, validating and folding each field.

    Args:
        headers: ``(name, value)`` pairs in the order they should appear.

    Returns:
        The serialized header block, with every line terminated by ``CRLF``.

    Raises:
        ValueError: Any header name or value is invalid.
    """
    parts = [fold_header(name, value) for name, value in headers]
    return "".join(parts).encode("utf-8")
