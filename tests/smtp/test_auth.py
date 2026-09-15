"""Tests for SASL AUTH encoding and selection, ported from mxraven-js."""

from __future__ import annotations

import pytest

from mxraven._internal.smtp.auth import encode_login, encode_plain, select_mechanism


def test_encode_plain() -> None:
    assert encode_plain("user", "pass") == "AHVzZXIAcGFzcw=="


def test_encode_plain_matches_base64_padding() -> None:
    assert encode_plain("f", "") == "AGYA"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", ""),
        ("f", "Zg=="),
        ("fo", "Zm8="),
        ("foo", "Zm9v"),
        ("foobar", "Zm9vYmFy"),
        ("Hello, World!", "SGVsbG8sIFdvcmxkIQ=="),
    ],
)
def test_encode_login(value: str, expected: str) -> None:
    assert encode_login(value) == expected


@pytest.mark.parametrize(
    ("preferred", "server", "expected"),
    [
        ([], [], ""),
        ([], ["plain", "login"], "PLAIN"),
        (["login"], ["PLAIN", "LOGIN"], "LOGIN"),
        (["XOAUTH2"], ["PLAIN", "LOGIN"], ""),
        ([], ["LOGIN"], "LOGIN"),
        ([], ["LOGIN", "PLAIN"], "PLAIN"),
        (["plain"], ["plain", "login"], "PLAIN"),
    ],
)
def test_select_mechanism(preferred: list[str], server: list[str], expected: str) -> None:
    assert select_mechanism(preferred, server) == expected
