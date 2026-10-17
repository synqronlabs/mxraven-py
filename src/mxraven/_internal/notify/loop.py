"""A private asyncio event loop on a worker thread.

Not part of the public API. The synchronous notification client uses it to run
the asyncio-only NATS transport while presenting a blocking API.
"""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Coroutine
    from concurrent.futures import Future

__all__ = ["LoopRunner"]

_T = TypeVar("_T")


class LoopRunner:
    """Runs one asyncio event loop on a daemon thread.

    Each client owns its own loop, so a blocking transport operation never
    contends with another client's.
    """

    __slots__ = ("_loop", "_ready", "_stopped", "_thread")

    def __init__(self, name: str) -> None:
        """Create a stopped runner owning a fresh event loop."""
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._ready = threading.Event()
        self._stopped = False

    def start(self) -> None:
        """Start the loop thread and wait until it is running."""
        self._thread.start()
        self._ready.wait()

    def submit(self, coro: Coroutine[object, object, _T]) -> Future[_T]:
        """Schedule `coro` on the loop and return its future."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def run(self, coro: Coroutine[object, object, _T], timeout: float | None = None) -> _T:
        """Schedule `coro` on the loop and block until it completes."""
        return self.submit(coro).result(timeout)

    def stop(self) -> None:
        """Ask the loop to stop. Safe to call more than once."""
        if self._stopped:
            return
        self._stopped = True
        self._loop.call_soon_threadsafe(self._loop.stop)

    def join(self, timeout: float | None = None) -> None:
        """Wait for the loop thread to finish."""
        self._thread.join(timeout)

    def close(self) -> None:
        """Close the event loop. Call after `stopped` and `join`."""
        self._loop.close()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()
        self._cancel_pending()

    def _cancel_pending(self) -> None:
        # A scheduled coroutine whose future is still awaited must not be left
        # pending, or the caller would block forever on a stopped loop.
        pending = asyncio.all_tasks(self._loop)
        for task in pending:
            task.cancel()
        if pending:
            self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        self._loop.run_until_complete(self._loop.shutdown_asyncgens())
