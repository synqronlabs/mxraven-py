"""Asynchronous mxRaven mail runtime.

Exposes :class:`mxraven.aio.Client`, the asyncio counterpart of
:class:`mxraven.Client`, with the same semantics and error surface.
"""

from __future__ import annotations

from mxraven.aio.client import Client

__all__ = ["Client"]
