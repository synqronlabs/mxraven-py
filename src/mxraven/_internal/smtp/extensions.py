"""SMTP extension parsing and capability inspection.

The EHLO reply advertises extensions as ``NAME`` or ``NAME params`` lines. The
first reply line carries the server greeting and is ignored. Capability
inspection drives which envelope parameters may be sent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = ["Capabilities", "parse_extensions"]

_WHITESPACE = (" ", "\t", "\r", "\n")
_SAFE_INTEGER_MAX = 2**53 - 1

SIZE = "SIZE"
PIPELINING = "PIPELINING"
EIGHT_BIT_MIME = "8BITMIME"
AUTH = "AUTH"
STARTTLS = "STARTTLS"
ENHANCED_STATUS_CODES = "ENHANCEDSTATUSCODES"
SMTPUTF8 = "SMTPUTF8"
DSN = "DSN"
CHUNKING = "CHUNKING"
BINARYMIME = "BINARYMIME"
DELIVERBY = "DELIVERBY"
REQUIRETLS = "REQUIRETLS"


def parse_extensions(lines: Sequence[str]) -> dict[str, str]:
    """Parse EHLO reply lines into an extension map.

    Args:
        lines: The EHLO reply message lines; the first is the greeting.

    Returns:
        The advertised extensions keyed by uppercase name. Names without a
        parameter map to an empty string; parameter values are not trimmed.
    """
    extensions: dict[str, str] = {}
    for line in lines[1:]:
        separator = line.find(" ")
        if separator == -1:
            extensions[line.upper()] = ""
        else:
            extensions[line[:separator].upper()] = line[separator + 1 :]
    return extensions


@dataclass(frozen=True, slots=True)
class Capabilities:
    """A read-only view of the extensions advertised by the server.

    Attributes:
        is_esmtp: Whether the server accepted ``EHLO``.
        hostname: The server hostname from the EHLO greeting.
        extensions: The extensions keyed by uppercase name.
        maximum_size: The advertised ``SIZE`` limit in bytes, or ``0``.
        auth_mechanisms: The advertised ``AUTH`` mechanisms, in order.
        delivery_by_min_seconds: The advertised ``DELIVERBY`` minimum, or ``0``.
    """

    is_esmtp: bool
    hostname: str
    extensions: Mapping[str, str]
    maximum_size: int
    auth_mechanisms: tuple[str, ...]
    delivery_by_min_seconds: int

    def has(self, name: str) -> bool:
        """Report whether an extension was advertised."""
        return name.upper() in self.extensions

    def param(self, name: str) -> str:
        """Return an extension's parameter, or an empty string."""
        return self.extensions.get(name.upper(), "")

    def supports_auth(self, mechanism: str) -> bool:
        """Report whether the server advertised an authentication mechanism."""
        return any(advertised.lower() == mechanism.lower() for advertised in self.auth_mechanisms)

    @property
    def starttls(self) -> bool:
        """Whether ``STARTTLS`` is advertised."""
        return self.has(STARTTLS)

    @property
    def pipelining(self) -> bool:
        """Whether ``PIPELINING`` is advertised."""
        return self.has(PIPELINING)

    @property
    def eight_bit_mime(self) -> bool:
        """Whether ``8BITMIME`` is advertised."""
        return self.has(EIGHT_BIT_MIME)

    @property
    def smtp_utf8(self) -> bool:
        """Whether ``SMTPUTF8`` is advertised."""
        return self.has(SMTPUTF8)

    @property
    def dsn(self) -> bool:
        """Whether ``DSN`` is advertised."""
        return self.has(DSN)

    @property
    def chunking(self) -> bool:
        """Whether ``CHUNKING`` is advertised."""
        return self.has(CHUNKING)

    @property
    def binary_mime(self) -> bool:
        """Whether ``BINARYMIME`` is advertised."""
        return self.has(BINARYMIME)

    @property
    def enhanced_status_codes(self) -> bool:
        """Whether ``ENHANCEDSTATUSCODES`` is advertised."""
        return self.has(ENHANCED_STATUS_CODES)

    @property
    def delivery_by(self) -> bool:
        """Whether ``DELIVERBY`` is advertised."""
        return self.has(DELIVERBY)

    @property
    def require_tls(self) -> bool:
        """Whether ``REQUIRETLS`` is advertised."""
        return self.has(REQUIRETLS)


def capabilities_from(
    extensions: Mapping[str, str], *, is_esmtp: bool, hostname: str
) -> Capabilities:
    """Build a capability view over an extension map.

    Args:
        extensions: The extensions keyed by uppercase name.
        is_esmtp: Whether ``EHLO`` succeeded.
        hostname: The server hostname from the EHLO greeting.

    Returns:
        A read-only capability view.
    """
    auth = tuple(value for value in extensions.get(AUTH, "").split() if value != "")
    return Capabilities(
        is_esmtp=is_esmtp,
        hostname=hostname,
        extensions=dict(extensions),
        maximum_size=_parse_positive_int(extensions.get(SIZE, "")),
        auth_mechanisms=auth,
        delivery_by_min_seconds=_parse_positive_int(extensions.get(DELIVERBY, "")),
    )


def _parse_positive_int(value: str) -> int:
    stripped = value.strip()
    if not stripped or not (stripped.isascii() and stripped.isdigit()):
        return 0
    parsed = int(stripped)
    return parsed if parsed <= _SAFE_INTEGER_MAX else 0
