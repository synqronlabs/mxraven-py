"""Handler contracts for the client notification consumer.

The same contracts are shared by :mod:`mxraven.notify` and
:mod:`mxraven.aio.notify`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, TypeAlias

if TYPE_CHECKING:
    from collections.abc import Callable

    from mxraven.notify.event import NotificationEvent

__all__ = [
    "STOP_CONSUMING",
    "NotificationHandler",
    "NotificationResult",
    "StopConsuming",
]


class StopConsuming:
    """The type of the :data:`STOP_CONSUMING` sentinel.

    Do not construct it directly; return the exported :data:`STOP_CONSUMING`
    singleton instead.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        """Return a readable representation."""
        return "STOP_CONSUMING"


STOP_CONSUMING: Final[StopConsuming] = StopConsuming()
"""Return this from a handler to acknowledge the current event and stop."""

NotificationResult: TypeAlias = "StopConsuming | None"
"""What a notification handler returns: ``None`` to acknowledge, or
:data:`STOP_CONSUMING` to acknowledge and stop."""

NotificationHandler: TypeAlias = "Callable[[NotificationEvent], NotificationResult]"
"""Processes one notification event synchronously.

Returning ``None`` acknowledges the event. Raising leaves it unacknowledged so
the server redelivers it with exponential backoff. Returning
:data:`STOP_CONSUMING` acknowledges the event and stops consumption.
"""
