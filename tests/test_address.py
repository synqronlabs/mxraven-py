"""Tests for RFC 5322 mailbox parsing and formatting, ported from mxraven-js."""

from __future__ import annotations

import pytest

from mxraven._internal.address import (
    MailboxAddress,
    contains_non_ascii,
    format_address,
    format_address_list,
    mailbox_to_string,
    parse_address,
)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("user@example.com", MailboxAddress("user", "example.com")),
        ("<a@b.co>", MailboxAddress("a", "b.co")),
        (
            "Acme <noreply@acme.example>",
            MailboxAddress("noreply", "acme.example", "Acme"),
        ),
        (
            '"Doe, John" <john@example.com>',
            MailboxAddress("john", "example.com", "Doe, John"),
        ),
        ('"john doe"@example.com', MailboxAddress("john doe", "example.com")),
        (
            "Jöhn <john@example.com>",
            MailboxAddress("john", "example.com", "Jöhn"),
        ),
    ],
)
def test_parse_address(source: str, expected: MailboxAddress) -> None:
    assert parse_address(source) == expected


@pytest.mark.parametrize(
    "source",
    [
        "",
        "   ",
        "a b@example.com",
        "user@",
        "@example.com",
        "user@exam ple.com",
        "a\nb@example.com",
        "Name <>",
        "Name <user@example.com> extra",
    ],
)
def test_parse_address_rejects(source: str) -> None:
    with pytest.raises(ValueError, match="mail:"):
        parse_address(source)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "user@example.com (Comment)",
            MailboxAddress("user", "example.com", "Comment"),
        ),
        (
            "John (middle) Doe <john@example.com>",
            MailboxAddress("john", "example.com", "John Doe"),
        ),
        (
            "John\r\n Doe <john@example.com>",
            MailboxAddress("john", "example.com", "John Doe"),
        ),
        (
            "John Q. Public <john@example.com>",
            MailboxAddress("john", "example.com", "John Q. Public"),
        ),
        ("(lead) user@example.com", MailboxAddress("user", "example.com")),
        ("< john@example.com >", MailboxAddress("john", "example.com")),
        ("user@[192.0.2.1]", MailboxAddress("user", "[192.0.2.1]")),
        (
            r"user@example.com (a \)b)",
            MailboxAddress("user", "example.com", "a )b"),
        ),
        (
            "(before) John Doe (after) <john@example.com>",
            MailboxAddress("john", "example.com", "John Doe"),
        ),
    ],
)
def test_parse_address_with_cfws(source: str, expected: MailboxAddress) -> None:
    assert parse_address(source) == expected


@pytest.mark.parametrize(
    "source",
    [
        "John\nDoe <john@example.com>",
        "user@example.com\r\nBcc: attacker@example.com",
        "John <john@example.com",
        "John Doe",
        "user@example.com extra",
        "(unterminated <john@example.com>",
    ],
)
def test_parse_address_with_cfws_rejects(source: str) -> None:
    with pytest.raises(ValueError, match="mail:"):
        parse_address(source)


def test_format_bare_address() -> None:
    assert format_address(MailboxAddress("user", "example.com")) == "user@example.com"


def test_format_display_name() -> None:
    address = MailboxAddress("noreply", "acme.example", "Acme")
    assert format_address(address) == "Acme <noreply@acme.example>"


def test_format_quotes_display_name_specials() -> None:
    address = MailboxAddress("john", "example.com", "Doe, John")
    assert format_address(address) == '"Doe, John" <john@example.com>'


def test_format_encodes_non_ascii_display_name() -> None:
    formatted = format_address(MailboxAddress("john", "example.com", "Jöhn"))
    assert formatted.startswith("=?UTF-8?B?")
    assert formatted.endswith("?= <john@example.com>")


def test_format_requotes_quoted_local_part() -> None:
    assert format_address(MailboxAddress("john doe", "example.com")) == '"john doe"@example.com'


def test_format_address_list() -> None:
    addresses = [MailboxAddress("a", "example.com"), MailboxAddress("b", "example.com")]
    assert format_address_list(addresses) == "a@example.com, b@example.com"


def test_mailbox_to_string_bare() -> None:
    assert mailbox_to_string(MailboxAddress("user", "example.com")) == "user@example.com"


def test_mailbox_to_string_empty() -> None:
    assert mailbox_to_string(MailboxAddress("", "")) == ""


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", False),
        ("hello world", False),
        ("hello123world", False),
        ("hello!@#$%^&*()_+-=", False),
        ("user@example.com", False),
        ("hello\r\nworld", False),
        ("hello\tworld", False),
        ("ä", True),
        ("hello wörld", True),
        ("hello 👋", True),
        ("你好", True),
        ("hello世界", True),
        ("user@exämple.com", True),
        (chr(0x80), True),
        (chr(127), False),
    ],
)
def test_contains_non_ascii(value: str, expected: bool) -> None:
    assert contains_non_ascii(value) is expected
