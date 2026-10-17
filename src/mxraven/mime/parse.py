"""Defensive parsing of inbound RFC 5322 / MIME messages.

The parser is intentionally lenient: real mail is frequently malformed, with
missing blank lines, ``LF``-only line endings, broken boundaries, and unknown
charsets. Malformed input yields the best available interpretation rather than
an exception. Only input that is structurally unbounded -- excessive nesting or
an excessive number of parts -- is rejected, so an untrusted message cannot
exhaust the stack or memory.

This is a port of the reference TypeScript and Go parsers so that a message is
interpreted identically across the mxRaven SDKs.
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING

from mxraven._internal.address import mailbox_to_string, parse_address
from mxraven.errors import MimeNestingError, MimePartsError

if TYPE_CHECKING:
    from datetime import datetime

__all__ = [
    "ParsedAddress",
    "ParsedAttachment",
    "ParsedEmail",
    "ParsedHeader",
    "parse_email",
]

_MAX_DEPTH = 20
_MAX_PARTS = 2000
_SKIP_CHARSETS = frozenset({"", "us-ascii", "ascii", "unknown-8bit"})
_CR = 0x0D
_LF = 0x0A
_SPACE = 0x20
_TAB = 0x09
_EQUALS = 0x3D
_ASCII_MAX = 0x7F
_DOT_DASH = 0x2D
_MIN_QUOTED_PARAM = 2
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
_BASE64_DIGITS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")
_ENCODED_WORD = re.compile(r"=\?([^?\s]+)\?([bBqQ])\?([^?]*)\?=")
_CONTINUATION = re.compile(r"(.*)\*(\d+)(\*)?\Z")
_LINE_SPLIT = re.compile(r"\r\n|\n|\r")
_QUOTED_PAIR = re.compile(r"\\(.)", re.DOTALL)


@dataclass(frozen=True, slots=True)
class ParsedHeader:
    """One header field from a parsed message.

    Attributes:
        name: The field name as it appeared in the message.
        value: The unfolded field value. Encoded words are not decoded; use
            :attr:`ParsedEmail.subject` or an address field for decoded text.
    """

    name: str
    value: str


@dataclass(frozen=True, slots=True)
class ParsedAddress:
    """One mailbox from a parsed address header.

    Attributes:
        address: The bare ``local@domain`` form.
        name: The display name, with RFC 2047 encoded words decoded, when one
            was present.
    """

    address: str
    name: str = ""


@dataclass(frozen=True, slots=True)
class ParsedAttachment:
    """One non-body MIME part.

    Attributes:
        content_type: The media type, for example ``application/pdf``.
        content: The decoded attachment bytes. It is a fresh copy that the
            caller owns and may mutate.
        filename: The decoded filename, when one was present.
        content_id: The ``Content-ID`` value without angle brackets, for inline
            parts.
        inline: Whether the part is marked ``inline`` or carries a
            ``Content-ID``.
    """

    content_type: str
    content: bytes
    filename: str = ""
    content_id: str = ""
    inline: bool = False


@dataclass(frozen=True, slots=True)
class ParsedEmail:
    """A parsed inbound RFC 5322 / MIME message.

    An empty value means the corresponding header or body was absent.

    Attributes:
        headers: Every header field, in order, with continuation lines
            unfolded. Values are not decoded.
        subject: The decoded ``Subject``, when the header was present.
        from_: The ``From`` addresses. It is `from_` because `from` is a
            Python keyword.
        to: The ``To`` addresses.
        cc: The ``Cc`` addresses.
        bcc: The ``Bcc`` addresses.
        reply_to: The ``Reply-To`` addresses.
        message_id: The ``Message-ID`` without angle brackets, when present.
        date: The parsed ``Date``, when present and parseable.
        text: The decoded first ``text/plain`` body, when the message has one.
        html: The decoded first ``text/html`` body, when the message has one.
        attachments: Every non-body part, with transfer encoding and filename
            decoding applied. ``message/rfc822`` parts appear here rather than
            being parsed recursively.
    """

    headers: tuple[ParsedHeader, ...] = ()
    subject: str = ""
    from_: tuple[ParsedAddress, ...] = ()
    to: tuple[ParsedAddress, ...] = ()
    cc: tuple[ParsedAddress, ...] = ()
    bcc: tuple[ParsedAddress, ...] = ()
    reply_to: tuple[ParsedAddress, ...] = ()
    message_id: str = ""
    date: datetime | None = None
    text: str = ""
    html: str = ""
    attachments: tuple[ParsedAttachment, ...] = ()


def parse_email(data: bytes | bytearray | memoryview) -> ParsedEmail:
    """Parse an inbound RFC 5322 / MIME message.

    The parser never validates the message against the grammar and never raises
    on malformed content; it extracts what it can. Multipart ``mixed``,
    ``alternative``, and ``related`` structures are traversed, ``base64`` and
    ``quoted-printable`` transfer encodings are decoded, RFC 2047 encoded words
    in structured headers are decoded, and RFC 2231 parameters are reassembled.
    ``message/rfc822`` parts are surfaced as attachments rather than parsed
    recursively.

    Args:
        data: The raw message bytes.

    Returns:
        The parsed message. Attachment content is a fresh copy.

    Raises:
        MimeNestingError: The message nests deeper than the parser allows.
        MimePartsError: The message has more MIME entities than the parser
            allows.

    Example:
        ```python
        from mxraven.mime import parse_email

        email = parse_email(raw_bytes)
        print(email.subject, len(email.attachments))
        ```
    """
    payload = bytes(data)
    state = _WalkState()
    root = _parse_entity(payload) if payload else None
    if root is not None:
        _walk_entity(root, 0, state)
    headers = root.headers if root is not None else ()

    return ParsedEmail(
        headers=headers,
        subject=_decode_optional_header(headers, "subject"),
        from_=_header_addresses(headers, "from"),
        to=_header_addresses(headers, "to"),
        cc=_header_addresses(headers, "cc"),
        bcc=_header_addresses(headers, "bcc"),
        reply_to=_header_addresses(headers, "reply-to"),
        message_id=_message_id(headers),
        date=_date_header(headers),
        text=state.text if state.text is not None else "",
        html=state.html if state.html is not None else "",
        attachments=tuple(state.attachments),
    )


@dataclass(slots=True)
class _MediaType:
    type: str
    subtype: str
    params: dict[str, str]


@dataclass(frozen=True, slots=True)
class _Entity:
    headers: tuple[ParsedHeader, ...]
    body: bytes


@dataclass(slots=True)
class _WalkState:
    text: str | None = None
    html: str | None = None
    attachments: list[ParsedAttachment] = field(default_factory=list)
    parts: int = 0


def _parse_entity(data: bytes) -> _Entity:
    header_end, body_start = _find_header_end(data)
    return _Entity(_parse_headers(data[:header_end]), data[body_start:])


def _find_header_end(data: bytes) -> tuple[int, int]:
    length = len(data)
    for index in range(length):
        byte = data[index]
        if byte not in (_LF, _CR):
            continue
        following = index + 1
        if byte == _CR and following < length and data[following] == _LF:
            following += 1
        if following >= length:
            continue
        upcoming = data[following]
        if upcoming == _CR:
            extra = 1 if following + 1 < length and data[following + 1] == _LF else 0
            return index, following + 1 + extra
        if upcoming == _LF:
            return index, following + 1
    return length, length


def _parse_headers(block: bytes) -> tuple[ParsedHeader, ...]:
    # Latin-1 keeps every byte, so a malformed header is not lost to a decode
    # error; structured decoding happens later.
    text = block.decode("latin-1")
    headers: list[ParsedHeader] = []
    current: ParsedHeader | None = None

    for line in _LINE_SPLIT.split(text):
        if line == "":
            continue
        if (line.startswith(" ") or line.startswith("\t")) and current is not None:
            current = ParsedHeader(current.name, current.value + line)
            headers[-1] = current
            continue
        colon = line.find(":")
        if colon <= 0:
            current = None
            continue
        name = line[:colon].strip()
        if name == "":
            current = None
            continue
        current = ParsedHeader(name, line[colon + 1 :].strip(" \t"))
        headers.append(current)
    return tuple(headers)


def _lookup(headers: tuple[ParsedHeader, ...], name: str) -> str | None:
    wanted = name.lower()
    for header in headers:
        if header.name.lower() == wanted:
            return header.value
    return None


def _walk_entity(entity: _Entity, depth: int, state: _WalkState) -> None:
    if depth > _MAX_DEPTH:
        message = "mime: message nesting is too deep"
        raise MimeNestingError(message)
    state.parts += 1
    if state.parts > _MAX_PARTS:
        message = "mime: too many MIME parts"
        raise MimePartsError(message)

    content_type = _parse_media_type(_lookup(entity.headers, "content-type"))
    if content_type.type == "multipart" and content_type.subtype != "":
        boundary = content_type.params.get("boundary", "")
        if boundary != "":
            for raw_part in _split_multipart(entity.body, boundary):
                if not raw_part:
                    continue
                _walk_entity(_parse_entity(raw_part), depth + 1, state)
            return

    encoding = _lookup(entity.headers, "content-transfer-encoding")
    disposition = _parse_media_type(_lookup(entity.headers, "content-disposition"))
    placement = (disposition.params.get("disposition") or disposition.type).lower()
    filename = disposition.params.get("filename") or content_type.params.get("name", "")
    content_id_header = _lookup(entity.headers, "content-id")
    content_id = _strip_angles(content_id_header.strip()) if content_id_header is not None else ""
    has_filename = filename != ""
    is_attachment = placement == "attachment" or has_filename or content_id != ""

    decoded = _decode_transfer_encoding(entity.body, encoding)

    if not is_attachment and content_type.type == "text" and content_type.subtype == "plain":
        if state.text is None:
            state.text = _decode_bytes(decoded, content_type.params.get("charset", ""))
        return
    if not is_attachment and content_type.type == "text" and content_type.subtype == "html":
        if state.html is None:
            state.html = _decode_bytes(decoded, content_type.params.get("charset", ""))
        return

    content_type_name = (
        f"{content_type.type}/{content_type.subtype}"
        if content_type.subtype != ""
        else content_type.type
    )
    state.attachments.append(
        ParsedAttachment(
            content_type=content_type_name,
            content=bytes(decoded),
            filename=_decode_header_text(filename) if has_filename else "",
            content_id=content_id,
            inline=placement == "inline" or (placement != "attachment" and content_id != ""),
        )
    )


def _strip_angles(value: str) -> str:
    if value.startswith("<"):
        value = value[1:]
    if value.endswith(">"):
        value = value[:-1]
    return value


_STOP = -1


def _split_multipart(body: bytes, boundary: str) -> list[bytes]:
    delimiter = b"--" + boundary.encode("latin-1", errors="replace")
    parts: list[bytes] = []
    position = 0
    part_start = -1

    while True:
        found = body.find(delimiter, position)
        if found == -1:
            if part_start != -1:
                parts.append(body[part_start:])
            return parts
        if found != 0 and body[found - 1] != _LF:
            position = found + len(delimiter)
            continue
        if part_start != -1:
            parts.append(body[part_start : _part_end(body, found)])
        offset = _scan_after_delimiter(body, found + len(delimiter))
        if offset == _STOP:
            return parts
        if offset is None:
            position = found + len(delimiter)
            continue
        part_start = offset
        position = offset


def _part_end(body: bytes, found: int) -> int:
    # The CRLF that precedes a boundary belongs to the boundary per RFC 2046,
    # so it is stripped from the part it terminates.
    end = found
    if found > 0 and body[found - 1] == _LF:
        end = found - 1
        if end > 0 and body[end - 1] == _CR:
            end -= 1
    return end


def _scan_after_delimiter(body: bytes, index: int) -> int | None:
    length = len(body)
    while index < length and body[index] in (_SPACE, _TAB):
        index += 1
    if index + 1 < length and body[index] == _DOT_DASH and body[index + 1] == _DOT_DASH:
        return _STOP
    if index + 1 < length and body[index] == _CR and body[index + 1] == _LF:
        return index + 2
    if index < length and body[index] == _LF:
        return index + 1
    if index >= length:
        return _STOP
    return None


def _parse_media_type(value: str | None) -> _MediaType:
    if value is None or value.strip() == "":
        return _MediaType("text", "plain", {})

    segments = _split_media_segments(value)
    primary, subtype = _split_primary((segments[0] if segments else "").strip().lower())
    plain = _collect_parameters(segments[1:])
    continuations: dict[str, list[tuple[int, bool, str]]] = {}
    params: dict[str, str] = {}

    for key, raw in plain:
        if "*" not in key:
            params[key] = raw
            continue
        if key.endswith("*") and "*" not in key[:-1]:
            params[key[:-1]] = _decode_extended_parameter(raw)
            continue
        match = _CONTINUATION.fullmatch(key)
        if match is None:
            continue
        base = match.group(1)
        index = int(match.group(2))
        encoded = match.group(3) == "*"
        continuations.setdefault(base, []).append((index, encoded, raw))

    for base, pieces in continuations.items():
        params[base] = _decode_continuations(pieces)

    return _MediaType(primary, subtype, params)


def _split_primary(main: str) -> tuple[str, str]:
    slash = main.find("/")
    if slash > 0:
        return main[:slash], main[slash + 1 :]
    return main, ""


def _collect_parameters(segments: list[str]) -> list[tuple[str, str]]:
    plain: list[tuple[str, str]] = []
    for raw_segment in segments:
        segment = raw_segment.strip()
        equals = segment.find("=")
        if equals <= 0:
            continue
        key = segment[:equals].strip().lower()
        raw = segment[equals + 1 :].strip()
        if len(raw) >= _MIN_QUOTED_PARAM and raw.startswith('"') and raw.endswith('"'):
            raw = _QUOTED_PAIR.sub(r"\1", raw[1:-1])
        plain.append((key, raw))
    return plain


def _decode_continuations(pieces: list[tuple[int, bool, str]]) -> str:
    pieces.sort(key=lambda piece: piece[0])
    buffer = bytearray()
    charset = "utf-8"
    for position, (_, encoded, raw) in enumerate(pieces):
        if position == 0 and encoded:
            first = raw.find("'")
            second = raw.find("'", first + 1) if first != -1 else -1
            if first > -1 and second > -1:
                charset = raw[:first] or "utf-8"
                buffer += _percent_decode(raw[second + 1 :])
            else:
                buffer += _percent_decode(raw)
            continue
        if encoded:
            buffer += _percent_decode(raw)
        else:
            for char in raw:
                buffer.append(ord(char) & 0xFF)
    return _decode_bytes(bytes(buffer), charset)


def _split_media_segments(value: str) -> list[str]:
    segments: list[str] = []
    current = ""
    quoted = False
    escaped = False
    for char in value:
        if escaped:
            current += char
            escaped = False
            continue
        if char == "\\" and quoted:
            current += char
            escaped = True
            continue
        if char == '"':
            quoted = not quoted
            current += char
            continue
        if char == ";" and not quoted:
            segments.append(current)
            current = ""
            continue
        current += char
    segments.append(current)
    return segments


def _decode_extended_parameter(value: str) -> str:
    first = value.find("'")
    second = value.find("'", first + 1) if first != -1 else -1
    if first > -1 and second > -1:
        charset = value[:first] or "utf-8"
        return _decode_bytes(bytes(_percent_decode(value[second + 1 :])), charset)
    return _decode_bytes(bytes(_percent_decode(value)), "utf-8")


def _percent_decode(value: str) -> bytearray:
    out = bytearray()
    index = 0
    length = len(value)
    while index < length:
        char = value[index]
        if char == "%" and index + 2 < length:
            pair = value[index + 1 : index + 3]
            if pair[0] in _HEX_DIGITS and pair[1] in _HEX_DIGITS:
                out.append(int(pair, 16))
                index += 3
                continue
        out.append(ord(char) & 0xFF)
        index += 1
    return out


def _decode_bytes(data: bytes, charset: str) -> str:
    label = charset.strip().lower()
    if label not in _SKIP_CHARSETS:
        try:
            return data.decode(label)
        except (LookupError, UnicodeDecodeError):
            pass
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _decode_header_text(value: str) -> str:
    text = value
    if any(ord(char) > _ASCII_MAX for char in value):
        raw = bytes(ord(char) & 0xFF for char in value)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = value
    return _decode_encoded_words(text)


def _decode_encoded_words(value: str) -> str:
    segments: list[tuple[bool, str]] = []
    last = 0
    for match in _ENCODED_WORD.finditer(value):
        start = match.start()
        if start > last:
            segments.append((False, value[last:start]))
        charset = match.group(1) or "utf-8"
        kind = (match.group(2) or "B").upper()
        payload = match.group(3) or ""
        if kind == "B":
            data = _decode_base64(payload.encode("latin-1", errors="replace"))
        else:
            data = _decode_q_encoded_word(payload)
        segments.append((True, _decode_bytes(data, charset)))
        last = match.end()
    if last < len(value):
        segments.append((False, value[last:]))

    out: list[str] = []
    for index, (encoded, text) in enumerate(segments):
        if not encoded and text.strip(" \t") == "":
            previous = segments[index - 1][0] if index > 0 else None
            following = segments[index + 1][0] if index + 1 < len(segments) else None
            if previous is True and following is True:
                continue
        out.append(text)
    return "".join(out)


def _decode_q_encoded_word(value: str) -> bytes:
    out = bytearray()
    index = 0
    length = len(value)
    while index < length:
        char = value[index]
        if char == "_":
            out.append(_SPACE)
            index += 1
            continue
        if char == "=" and index + 2 < length:
            pair = value[index + 1 : index + 3]
            if pair[0] in _HEX_DIGITS and pair[1] in _HEX_DIGITS:
                out.append(int(pair, 16))
                index += 3
                continue
        out.append(ord(char) & 0xFF)
        index += 1
    return bytes(out)


def _decode_transfer_encoding(data: bytes, encoding: str | None) -> bytes:
    label = (encoding or "").strip().lower()
    if label == "base64":
        return _decode_base64(data)
    if label == "quoted-printable":
        return _decode_quoted_printable(data)
    return data


def _decode_base64(data: bytes) -> bytes:
    filtered = bytes(byte for byte in data if chr(byte) in _BASE64_DIGITS)
    remainder = len(filtered) % 4
    if remainder == 1:
        filtered = filtered[:-1]
        remainder = 0
    if remainder:
        filtered += b"=" * (4 - remainder)
    try:
        return base64.b64decode(filtered, validate=False)
    except (binascii.Error, ValueError):
        return b""


def _decode_quoted_printable(data: bytes) -> bytes:
    out = bytearray()
    index = 0
    length = len(data)
    while index < length:
        byte = data[index]
        if byte != _EQUALS:
            out.append(byte)
            index += 1
            continue
        following = data[index + 1] if index + 1 < length else None
        if following == _CR and index + 2 < length and data[index + 2] == _LF:
            index += 3
            continue
        if following == _LF:
            index += 2
            continue
        if following is None:
            break
        low = data[index + 2] if index + 2 < length else None
        if chr(following) in _HEX_DIGITS and low is not None and chr(low) in _HEX_DIGITS:
            out.append(int(chr(following) + chr(low), 16))
            index += 3
            continue
        out.append(byte)
        index += 1
    return bytes(out)


def _decode_optional_header(headers: tuple[ParsedHeader, ...], name: str) -> str:
    value = _lookup(headers, name)
    return _decode_header_text(value) if value is not None else ""


def _message_id(headers: tuple[ParsedHeader, ...]) -> str:
    value = _lookup(headers, "message-id")
    if value is None:
        return ""
    return _strip_angles(value.strip())


def _date_header(headers: tuple[ParsedHeader, ...]) -> datetime | None:
    value = _lookup(headers, "date")
    if value is None or value.strip() == "":
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None


def _header_addresses(headers: tuple[ParsedHeader, ...], name: str) -> tuple[ParsedAddress, ...]:
    value = _lookup(headers, name)
    if value is None or value.strip() == "":
        return ()
    addresses: list[ParsedAddress] = []
    for piece in _split_address_list(value):
        trimmed = piece.strip()
        if trimmed == "":
            continue
        addresses.append(_parse_one_address(trimmed))
    return tuple(addresses)


def _split_address_list(value: str) -> list[str]:
    pieces: list[str] = []
    current = ""
    quoted = False
    angle_depth = 0
    comment_depth = 0
    escaped = False
    for char in value:
        if escaped:
            current += char
            escaped = False
            continue
        if char == "\\":
            current += char
            escaped = True
            continue
        if char == '"':
            quoted = not quoted
            current += char
            continue
        if not quoted:
            if char == "<":
                angle_depth += 1
            elif char == ">":
                angle_depth = angle_depth - 1 if angle_depth > 0 else 0
            elif char == "(":
                comment_depth += 1
            elif char == ")":
                comment_depth = comment_depth - 1 if comment_depth > 0 else 0
            elif char == "," and angle_depth == 0 and comment_depth == 0:
                pieces.append(current)
                current = ""
                continue
        current += char
    pieces.append(current)
    return pieces


def _parse_one_address(piece: str) -> ParsedAddress:
    try:
        mailbox = parse_address(piece)
    except ValueError:
        open_index = piece.rfind("<")
        close_index = piece.rfind(">")
        if open_index >= 0 and close_index > open_index:
            return ParsedAddress(piece[open_index + 1 : close_index].strip())
        return ParsedAddress(piece.strip())
    name = _decode_header_text(mailbox.display_name) if mailbox.display_name else ""
    return ParsedAddress(mailbox_to_string(mailbox), name)
