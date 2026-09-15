"""Smoke tests that the package imports and reports a version."""

from __future__ import annotations

import mxraven


def test_version_is_a_non_empty_string() -> None:
    assert isinstance(mxraven.__version__, str)
    assert mxraven.__version__
