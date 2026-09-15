"""Tests for the public package surface, ported from mxraven-js index.test.ts."""

from __future__ import annotations

import mxraven
from mxraven import DEFAULT_ADDRESS_PORT


def test_default_address_port() -> None:
    assert DEFAULT_ADDRESS_PORT == 587


def test_public_names_are_importable() -> None:
    for name in mxraven.__all__:
        assert hasattr(mxraven, name)
