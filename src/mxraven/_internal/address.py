"""RFC 5322 mailbox parsing and formatting.

Internal to the SDK. Implements the RFC 5322 mailbox grammar, including
comments and folding whitespace (``CFWS``), quoted strings, quoted local parts,
domain literals, and internationalized addresses. Group syntax
(``display-name: mailbox-list;``) is not supported.

This is a direct port of the reference TypeScript parser so that a mailbox is
accepted or rejected identically across the mxRaven SDKs. Invalid input raises
:class:`ValueError` with a ``mail:`` prefix.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Never

__all__ = [
    "MailboxAddress",
    "contains_non_ascii",
    "encode_rfc2047",
    "format_address",
    "format_address_list",
    "mailbox_to_string",
    "parse_address",
]

_NON_ASCII = 0x80
_CONTROL_MAX = 0x20
_PRINTABLE_MIN = 33
_PRINTABLE_MAX = 126
_ATEXT_SPECIALS = frozenset("!#$%&'*+-/=?^_`{|}~")
_DISPLAY_SPECIALS = frozenset('!"(),.:;<>@[\\]')


@dataclass(frozen=True, slots=True)
class MailboxAddress:
    """A parsed mailbox address.

    Attributes:
        local_part: The local part, without quoting.
        domain: The domain, including brackets for a domain literal.
        display_name: The display name, when one was supplied.
    """

    local_part: str
    domain: str
    display_name: str | None = None


def contains_non_ascii(value: str) -> bool:
    """Report whether a string contains a non-ASCII code point."""
    return any(ord(char) >= _NON_ASCII for char in value)


def mailbox_to_string(address: MailboxAddress) -> str:
    r"""Return the bare ``local@domain`` form of an address.

    An empty mailbox renders as an empty string. A local part that needs
    quoting is wrapped in double quotes with ``"`` and ``\`` escaped.
    """
    if address.local_part == "" and address.domain == "":
        return ""
    local = address.local_part
    if _needs_quoting(local):
        escaped = local.replace("\\", "\\\\").replace('"', '\\"')
        local = f'"{escaped}"'
    return f"{local}@{address.domain}"


def parse_address(source: str) -> MailboxAddress:
    """Parse a single mailbox address.

    Accepts the RFC 5322 ``mailbox`` productions — ``addr-spec`` and
    ``[display-name] angle-addr`` — with comments and folding whitespace
    anywhere the grammar permits. Comments are discarded except for a trailing
    comment after a bare ``addr-spec``, which becomes the display name.

    Args:
        source: The address text.

    Returns:
        The parsed mailbox.

    Raises:
        ValueError: The input contains a line break, is empty, or is not a
            valid mailbox.
    """
    return _AddressParser(source).parse()


def format_address(address: MailboxAddress) -> str:
    """Format one address for use in a header field."""
    email = mailbox_to_string(address)
    name = address.display_name
    if name is None or name == "":
        return email
    if contains_non_ascii(name):
        return f"{encode_rfc2047(name)} <{email}>"
    if any(char in _DISPLAY_SPECIALS for char in name):
        escaped = name.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}" <{email}>'
    return f"{name} <{email}>"


def format_address_list(addresses: list[MailboxAddress]) -> str:
    """Format several addresses as a comma-separated header value."""
    return ", ".join(format_address(address) for address in addresses)


def encode_rfc2047(value: str) -> str:
    """Encode a string using RFC 2047 Base64 (``=?UTF-8?B?...?=``)."""
    encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
    return f"=?UTF-8?B?{encoded}?="


def _is_atext(char: str) -> bool:
    return char.isascii() and (char.isalnum() or char in _ATEXT_SPECIALS)


def _needs_quoting(local_part: str) -> bool:
    if contains_non_ascii(local_part):
        return False
    if local_part.startswith(".") or local_part.endswith(".") or ".." in local_part:
        return True
    return any(char != "." and not _is_atext(char) for char in local_part)


def _join_phrase(words: list[str]) -> str:
    result = ""
    for word in words:
        if word == ".":
            result += "."
        elif result == "":
            result = word
        else:
            result += f" {word}"
    return result


def _append_word_separator(text: str) -> str:
    if text == "" or text.endswith(" "):
        return text
    return f"{text} "


class _AddressParser:
    """A recursive-descent parser for one RFC 5322 mailbox."""

    def __init__(self, source: str) -> None:
        self._source = source
        self._pos = 0

    def parse(self) -> MailboxAddress:
        start = self._pos

        self._skip_cfws()
        if self._peek() == "<":
            angle = self._parse_angle_addr()
            self._skip_cfws()
            self._require_end()
            return angle

        self._pos = start
        self._skip_cfws()
        spec = self._try_addr_spec()
        if spec is not None:
            name = self._consume_trailing_comment()
            if self._at_end():
                if name is None:
                    return spec
                return MailboxAddress(spec.local_part, spec.domain, name)

        self._pos = start
        self._skip_cfws()
        display_name = self._parse_phrase()
        self._skip_cfws()
        if self._peek() != "<":
            self._fail(f"invalid address {self._source!r}")
        angle = self._parse_angle_addr()
        self._skip_cfws()
        self._require_end()
        if display_name == "":
            return angle
        return MailboxAddress(angle.local_part, angle.domain, display_name)

    def _try_addr_spec(self) -> MailboxAddress | None:
        save = self._pos
        try:
            return self._parse_addr_spec()
        except ValueError:
            self._pos = save
            return None

    def _parse_addr_spec(self) -> MailboxAddress:
        local_part = self._parse_local_part()
        self._skip_cfws()
        self._expect("@")
        self._skip_cfws()
        domain = self._parse_domain()
        return MailboxAddress(local_part, domain)

    def _parse_local_part(self) -> str:
        if self._peek() == '"':
            return self._parse_quoted_string()
        return self._parse_dot_atom_text()

    def _parse_domain(self) -> str:
        if self._peek() == "[":
            return self._parse_domain_literal()
        return self._parse_dot_atom_text()

    def _parse_angle_addr(self) -> MailboxAddress:
        self._skip_cfws()
        self._expect("<")
        self._skip_cfws()
        spec = self._parse_addr_spec()
        self._skip_cfws()
        self._expect(">")
        return spec

    def _parse_phrase(self) -> str:
        words: list[str] = []
        while True:
            self._skip_cfws()
            char = self._peek()
            if char is None or char in ("<", ":", "@", ","):
                break
            if char == ".":
                words.append(".")
                self._pos += 1
                continue
            if char == '"':
                words.append(self._parse_quoted_string())
                continue
            words.append(self._parse_atom(allow_utf8=True))
        return _join_phrase(words)

    def _parse_atom(self, *, allow_utf8: bool) -> str:
        start = self._pos
        while True:
            char = self._peek()
            if char is None:
                break
            if _is_atext(char) or (allow_utf8 and ord(char) >= _NON_ASCII):
                self._pos += 1
                continue
            break
        if self._pos == start:
            self._fail(f"expected an atom in {self._source!r}")
        return self._source[start : self._pos]

    def _parse_dot_atom_text(self) -> str:
        value = self._parse_atom(allow_utf8=True)
        while self._peek() == ".":
            self._pos += 1
            value += f".{self._parse_atom(allow_utf8=True)}"
        return value

    def _parse_quoted_string(self) -> str:
        self._expect('"')
        value = ""
        while True:
            char = self._peek()
            if char is None:
                self._fail(f"unterminated quoted string in {self._source!r}")
            if char == '"':
                self._pos += 1
                return value
            if char == "\\":
                self._pos += 1
                escaped = self._peek()
                if escaped is None or escaped in ("\r", "\n", "\x00"):
                    self._fail(f"invalid quoted pair in {self._source!r}")
                value += escaped
                self._pos += 1
                continue
            if char in ("\r", "\n"):
                value += self._consume_folded_whitespace()
                continue
            if ord(char) < _CONTROL_MAX and char != "\t":
                self._fail(f"invalid character in quoted string {self._source!r}")
            value += char
            self._pos += 1

    def _parse_domain_literal(self) -> str:
        self._expect("[")
        value = ""
        while True:
            char = self._peek()
            if char is None:
                self._fail(f"unterminated domain literal in {self._source!r}")
            if char == "]":
                self._pos += 1
                return f"[{value}]"
            if char == "\\":
                self._pos += 1
                escaped = self._peek()
                if escaped is None or escaped in ("\r", "\n"):
                    self._fail("invalid quoted pair in domain literal")
                value += escaped
                self._pos += 1
                continue
            code = ord(char)
            if code < _PRINTABLE_MIN or code > _PRINTABLE_MAX or char == "[":
                self._fail(f"invalid character in domain literal {self._source!r}")
            value += char
            self._pos += 1

    def _consume_trailing_comment(self) -> str | None:
        name: str | None = None
        while True:
            self._skip_fws()
            if self._peek() != "(":
                break
            text = self._consume_comment()
            if name is None and text != "":
                name = text
        return name

    def _consume_comment(self) -> str:
        self._expect("(")
        depth = 1
        text = ""
        while True:
            char = self._peek()
            if char is None:
                self._fail(f"unterminated comment in {self._source!r}")
            if char == "(":
                depth += 1
                self._pos += 1
                text = _append_word_separator(text)
                continue
            if char == ")":
                depth -= 1
                self._pos += 1
                if depth == 0:
                    return text.strip()
                text = _append_word_separator(text)
                continue
            if char == "\\":
                self._pos += 1
                escaped = self._peek()
                if escaped is None or escaped in ("\r", "\n", "\x00"):
                    self._fail("invalid quoted pair in comment")
                text += escaped
                self._pos += 1
                continue
            if char in ("\r", "\n"):
                text += self._consume_folded_whitespace()
                continue
            if ord(char) < _CONTROL_MAX and char != "\t":
                self._fail(f"invalid character in comment {self._source!r}")
            text += char
            self._pos += 1

    def _skip_cfws(self) -> None:
        while True:
            self._skip_fws()
            if self._peek() == "(":
                self._consume_comment()
                continue
            break

    def _skip_fws(self) -> None:
        while True:
            char = self._peek()
            if char in (" ", "\t"):
                self._pos += 1
                continue
            if char in ("\r", "\n"):
                self._consume_folded_whitespace()
                continue
            break

    def _consume_folded_whitespace(self) -> str:
        if self._peek() == "\r":
            if self._lookahead(1) != "\n":
                self._fail("bare carriage return")
            if self._lookahead(2) not in (" ", "\t"):
                self._fail("folding without trailing whitespace")
            self._pos += 3
        else:
            self._fail("bare line feed")
        while self._peek() in (" ", "\t"):
            self._pos += 1
        return " "

    def _expect(self, char: str) -> None:
        if self._peek() != char:
            self._fail(f"expected {char!r} in {self._source!r}")
        self._pos += 1

    def _require_end(self) -> None:
        if not self._at_end():
            self._fail(f"unexpected text after address in {self._source!r}")

    def _at_end(self) -> bool:
        return self._pos >= len(self._source)

    def _peek(self) -> str | None:
        if self._pos < len(self._source):
            return self._source[self._pos]
        return None

    def _lookahead(self, offset: int) -> str | None:
        index = self._pos + offset
        if 0 <= index < len(self._source):
            return self._source[index]
        return None

    def _fail(self, message: str) -> Never:
        text = f"mail: {message}"
        raise ValueError(text)
