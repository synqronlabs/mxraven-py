"""RFC 3461 Delivery Status Notification parameter formatting.

Only the encoding direction is needed when building commands; xtext parsing is
included for symmetry and testing.

This is a port of the reference TypeScript helpers so that parameter encoding
is byte-identical across the mxRaven SDKs.
"""

from __future__ import annotations

__all__ = [
    "encode_xtext",
    "format_envelope_id",
    "format_original_recipient",
    "normalize_notify",
    "normalize_return",
    "parse_xtext",
]

_MAX_ENVELOPE_ID_LENGTH = 100
_MAX_ORIGINAL_RECIPIENT_LENGTH = 500
_PRINTABLE_MIN = 0x21
_PRINTABLE_MAX = 0x7E
_DELETE = 0x7F
_TAB = 0x09
_PLUS = 0x2B
_EQUALS = 0x3D
_BACKSLASH = 0x5C
_CONTROL_MAX = 0x20
_ASCII_MAX = 0x80
_ATOM_SPECIALS = frozenset("!#$%&'*+-/?^_`{|}~")
_NOTIFY_VALUES = frozenset({"NEVER", "SUCCESS", "FAILURE", "DELAY"})
_ENVELOPE_ID_PREFIX = "ENVID="
_ORIGINAL_RECIPIENT_PREFIX = "ORCPT="


def parse_xtext(wire: str) -> str:
    """Parse an RFC 3461 xtext value.

    Args:
        wire: The encoded value.

    Returns:
        The decoded value.

    Raises:
        ValueError: The value contains an invalid escape or character.
    """
    decoded = ""
    index = 0
    while index < len(wire):
        char = wire[index]
        if char == "+":
            if index + 2 >= len(wire):
                message = "dsn: invalid xtext escape"
                raise ValueError(message)
            high = _from_hex(wire[index + 1])
            low = _from_hex(wire[index + 2])
            if high is None or low is None:
                message = "dsn: invalid xtext escape"
                raise ValueError(message)
            decoded += chr((high << 4) | low)
            index += 3
            continue
        code = ord(char)
        if code < _PRINTABLE_MIN or code > _PRINTABLE_MAX or char == "=":
            message = "dsn: invalid xtext character"
            raise ValueError(message)
        decoded += char
        index += 1
    return decoded


def encode_xtext(decoded: str) -> str:
    """Encode a value as RFC 3461 xtext."""
    encoded = ""
    for char in decoded:
        code = ord(char)
        if _PRINTABLE_MIN <= code <= _PRINTABLE_MAX and code not in (_PLUS, _EQUALS):
            encoded += char
            continue
        encoded += f"+{code >> 4:X}{code & 0x0F:X}"
    return encoded


def normalize_return(value: str) -> str:
    """Validate and upper-case a ``RET`` value.

    Args:
        value: ``FULL`` or ``HDRS``, case-insensitively.

    Returns:
        The canonical value.

    Raises:
        ValueError: The value is not ``FULL`` or ``HDRS``.
    """
    if "\r" in value or "\n" in value:
        message = "dsn: RET contains a line break"
        raise ValueError(message)
    normalized = value.upper()
    if normalized not in ("FULL", "HDRS"):
        message = "dsn: RET must be FULL or HDRS"
        raise ValueError(message)
    return normalized


def normalize_notify(values: list[str]) -> list[str]:
    """Validate and upper-case ``NOTIFY`` values.

    ``NEVER`` is valid only as the sole value.

    Args:
        values: The values to normalize.

    Returns:
        The canonical values.

    Raises:
        ValueError: A value is invalid or ``NEVER`` is combined with others.
    """
    if not values:
        message = "dsn: NOTIFY value is empty"
        raise ValueError(message)
    normalized: list[str] = []
    for value in values:
        if value == "" or "\r" in value or "\n" in value:
            message = "dsn: invalid NOTIFY value"
            raise ValueError(message)
        upper = value.upper()
        if upper not in _NOTIFY_VALUES:
            message = f"dsn: invalid NOTIFY value {value!r}"
            raise ValueError(message)
        normalized.append(upper)
    if "NEVER" in normalized and len(normalized) != 1:
        message = "dsn: NOTIFY NEVER must appear alone"
        raise ValueError(message)
    return normalized


def format_envelope_id(decoded: str) -> str:
    """Encode a decoded envelope identifier as its ``ENVID`` wire value.

    Args:
        decoded: The printable-ASCII envelope identifier.

    Returns:
        The encoded value, without the ``ENVID=`` prefix.

    Raises:
        ValueError: The value is empty, non-printable, or too long.
    """
    if decoded == "" or not _is_printable_ascii(decoded):
        message = "dsn: invalid ENVID decoded value"
        raise ValueError(message)
    wire = encode_xtext(decoded)
    if len(wire) + len(_ENVELOPE_ID_PREFIX) > _MAX_ENVELOPE_ID_LENGTH:
        message = f"dsn: ENVID exceeds {_MAX_ENVELOPE_ID_LENGTH} characters"
        raise ValueError(message)
    return wire


def format_original_recipient(address_type: str, decoded: str, *, smtp_utf8: bool) -> str:
    """Encode a decoded original recipient as its ``ORCPT`` wire value.

    Args:
        address_type: The address type, for example ``rfc822`` or ``utf-8``.
        decoded: The decoded original recipient.
        smtp_utf8: Whether the session negotiated ``SMTPUTF8``.

    Returns:
        The ``address-type;encoded-address`` value.

    Raises:
        ValueError: The address type or value is invalid.
    """
    if not _is_atom(address_type):
        message = "dsn: invalid ORCPT address type"
        raise ValueError(message)

    if address_type.lower() == "utf-8":
        encoded = _encode_utf8_orcpt(decoded, smtp_utf8=smtp_utf8)
    else:
        if decoded == "" or not _is_printable_ascii(decoded):
            message = "dsn: invalid ORCPT decoded address"
            raise ValueError(message)
        encoded = encode_xtext(decoded)

    wire = f"{address_type};{encoded}"
    if len(wire) + len(_ORIGINAL_RECIPIENT_PREFIX) > _MAX_ORIGINAL_RECIPIENT_LENGTH:
        message = f"dsn: ORCPT exceeds {_MAX_ORIGINAL_RECIPIENT_LENGTH} characters"
        raise ValueError(message)
    return wire


def _encode_utf8_orcpt(address: str, *, smtp_utf8: bool) -> str:
    if address == "" or "\r" in address or "\n" in address:
        message = "dsn: invalid UTF-8 ORCPT address"
        raise ValueError(message)
    encoded = ""
    for char in address:
        code = ord(char)
        if code < _ASCII_MAX and _is_qchar(code):
            encoded += char
            continue
        if code >= _ASCII_MAX and smtp_utf8:
            encoded += char
            continue
        encoded += f"\\x{{{code:X}}}"
    return encoded


def _is_printable_ascii(value: str) -> bool:
    if value == "":
        return False
    for char in value:
        code = ord(char)
        if code != _TAB and (code < _CONTROL_MAX or code > _PRINTABLE_MAX):
            return False
    return True


def _is_atom(value: str) -> bool:
    if value == "":
        return False
    for char in value:
        if char.isascii() and char.isalnum():
            continue
        if char in _ATOM_SPECIALS:
            continue
        return False
    return True


def _is_qchar(code: int) -> bool:
    return _PRINTABLE_MIN <= code <= _PRINTABLE_MAX and code not in (_BACKSLASH, _PLUS, _EQUALS)


def _from_hex(char: str) -> int | None:
    if "0" <= char <= "9":
        return ord(char) - 0x30
    if "A" <= char <= "F":
        return ord(char) - 0x37
    return None
