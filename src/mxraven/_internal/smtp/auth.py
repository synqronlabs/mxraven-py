"""SASL AUTH encoding and mechanism selection.

Pure helpers shared by the synchronous and asynchronous drivers.
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["encode_login", "encode_plain", "select_mechanism"]

_PREFERENCE = ("PLAIN", "LOGIN")
_PLAIN = "PLAIN"
_LOGIN = "LOGIN"


def encode_plain(username: str, password: str) -> str:
    r"""Encode a SASL ``PLAIN`` response as Base64 (``\0user\0pass``)."""
    credentials = f"\x00{username}\x00{password}".encode()
    return base64.b64encode(credentials).decode("ascii")


def encode_login(value: str) -> str:
    """Encode one SASL ``LOGIN`` challenge response as Base64."""
    return base64.b64encode(value.encode()).decode("ascii")


def select_mechanism(preferred: Sequence[str], server: Sequence[str]) -> str:
    """Select a SASL mechanism from the server's advertised list.

    Args:
        preferred: Client preferences in order. When empty, ``PLAIN`` then
            ``LOGIN`` are preferred.
        server: The mechanisms the server advertised.

    Returns:
        The canonical uppercase mechanism name, or an empty string when none
        is supported.
    """
    lower = [value.lower() for value in server]
    candidates = [value.upper() for value in preferred] if preferred else list(_PREFERENCE)
    for candidate in candidates:
        if candidate.lower() in lower:
            return candidate
    return ""
