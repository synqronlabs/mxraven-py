"""Tests for EHLO extension parsing and capability inspection."""

from __future__ import annotations

from mxraven._internal.smtp.extensions import capabilities_from, parse_extensions


def test_parse_extensions_basic() -> None:
    lines = [
        "mock.example.com Hello",
        "SIZE 10485760",
        "PIPELINING",
        "8BITMIME",
        "AUTH PLAIN LOGIN",
        "STARTTLS",
        "ENHANCEDSTATUSCODES",
        "SMTPUTF8",
        "DSN",
        "CHUNKING",
        "BINARYMIME",
        "DELIVERBY 300",
    ]
    extensions = parse_extensions(lines)
    assert extensions["SIZE"] == "10485760"
    assert extensions["PIPELINING"] == ""
    assert extensions["AUTH"] == "PLAIN LOGIN"
    assert extensions["DELIVERBY"] == "300"


def test_parse_extensions_greeting_only() -> None:
    assert parse_extensions(["mock.example.com Hello"]) == {}


def test_capabilities() -> None:
    extensions = parse_extensions(
        [
            "mock.example.com Hello",
            "SIZE 52428800",
            "PIPELINING",
            "8BITMIME",
            "AUTH PLAIN LOGIN",
            "DELIVERBY 300",
        ]
    )
    caps = capabilities_from(extensions, is_esmtp=True, hostname="mock.example.com")
    assert caps.is_esmtp is True
    assert caps.hostname == "mock.example.com"
    assert caps.pipelining is True
    assert caps.eight_bit_mime is True
    assert caps.delivery_by is True
    assert caps.delivery_by_min_seconds == 300
    assert caps.maximum_size == 52428800
    assert caps.auth_mechanisms == ("PLAIN", "LOGIN")
    assert caps.supports_auth("plain") is True
    assert caps.supports_auth("CRAM-MD5") is False


def test_capabilities_invalid_size() -> None:
    extensions = parse_extensions(["mock.example.com Hello", "SIZE notanumber"])
    caps = capabilities_from(extensions, is_esmtp=True, hostname="mock.example.com")
    assert caps.maximum_size == 0


def test_capabilities_empty() -> None:
    caps = capabilities_from({}, is_esmtp=False, hostname="")
    assert caps.auth_mechanisms == ()
    assert caps.supports_auth("PLAIN") is False
    assert caps.maximum_size == 0


def test_parse_single_extension() -> None:
    extensions = parse_extensions(["mock.example.com", "PIPELINING"])
    assert "PIPELINING" in extensions


def test_capabilities_empty_size_parameter() -> None:
    caps = capabilities_from({"SIZE": ""}, is_esmtp=False, hostname="")
    assert caps.maximum_size == 0


def test_capabilities_binary_and_enhanced() -> None:
    caps = capabilities_from(
        {"BINARYMIME": "", "ENHANCEDSTATUSCODES": "", "PIPELINING": ""},
        is_esmtp=True,
        hostname="mock.example.com",
    )
    assert caps.binary_mime is True
    assert caps.enhanced_status_codes is True
    assert caps.has("PIPELINING") is True
    assert caps.has("X-NOT-ADVERTISED") is False
    assert caps.param("PIPELINING") == ""
