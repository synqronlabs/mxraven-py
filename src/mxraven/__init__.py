"""The official Python SDK for the mxRaven mail runtime.

The root package exposes SMTP submission. The webhook and feedback runtime
surfaces live in :mod:`mxraven.webhook` and :mod:`mxraven.feedback`.

Control-plane administration (tenants, domains, suppressions, analytics) is out
of scope and is served by the separate mxRaven control-plane SDK.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

__all__ = ["__version__"]

try:
    __version__ = version("mxraven")
except PackageNotFoundError:  # pragma: no cover - source tree without metadata
    __version__ = "0.0.0"
