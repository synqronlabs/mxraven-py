"""The notification transport is an optional extra."""

from __future__ import annotations

import builtins
import sys
from typing import TYPE_CHECKING

import pytest

from mxraven._internal.notify.config import load_open_session
from mxraven.errors import NotificationError

if TYPE_CHECKING:
    from pytest import MonkeyPatch


def test_missing_extra_reports_install_guidance(monkeypatch: MonkeyPatch) -> None:
    """A missing NATS transport raises a NotificationError, not ImportError."""
    real_import = builtins.__import__

    def blocked(name: str, *args: object, **kwargs: object) -> object:
        if name == "nats" or name.startswith("nats."):
            message = f"No module named {name!r}"
            raise ModuleNotFoundError(message, name=name)
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]  # variadic passthrough

    monkeypatch.setattr(builtins, "__import__", blocked)
    for name in list(sys.modules):
        if name == "nats" or name.startswith("nats.") or name == "mxraven._internal.notify.session":
            monkeypatch.delitem(sys.modules, name, raising=False)

    with pytest.raises(NotificationError, match="notify"):
        load_open_session()
