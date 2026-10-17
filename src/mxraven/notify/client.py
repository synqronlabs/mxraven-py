"""Synchronous mxRaven client notification consumer.

Consumes the durable mail events a listener publishes over NATS JetStream,
using the same API key as SMTP submission. The transport requires the optional
``notify`` extra: install ``mxraven[notify]``.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from typing import TYPE_CHECKING, Self, TypeAlias

from mxraven._internal.notify.config import DEFAULT_CONNECT_TIMEOUT, validate_options
from mxraven._internal.notify.loop import LoopRunner
from mxraven.errors import NotificationError

if TYPE_CHECKING:
    import ssl
    from collections.abc import Callable
    from concurrent.futures import Future

    from mxraven.aio.notify.client import (
        NotificationClient as _AsyncNotificationClient,
    )
    from mxraven.aio.notify.client import (
        NotificationDelivery as _AsyncDelivery,
    )
    from mxraven.aio.notify.client import (
        NotificationIterator as _AsyncIterator,
    )
    from mxraven.notify.event import NotificationEvent
    from mxraven.notify.handler import NotificationResult

__all__ = ["NotificationClient", "NotificationDelivery", "NotificationIterator"]

NotificationHandler: TypeAlias = "Callable[[NotificationEvent], NotificationResult]"
ErrorHandler: TypeAlias = "Callable[[NotificationError], None]"


class NotificationClient:
    """Consumes one listener's client notification events synchronously.

    Notifications are off by default; enable them for the listener with the
    mxRaven administrative API first, which creates the durable consumer this
    client reads from. The connection authenticates with the listener's
    ordinary API key and is granted access only to that listener's consumer.

    A client is safe for concurrent use. A `consume` call and an `events`
    iterator share the durable consumer with any other client using the same
    API key: events are distributed between them, not duplicated to each.

    This client owns a private asyncio event loop on a worker thread. Handlers
    run on a worker thread, not the calling thread, so a slow handler does not
    block `close` from another thread. Do not call `close` from the thread that
    is blocked in `consume`.

    Use :meth:`connect` to build a connected client; do not construct one
    directly. A client is a context manager.

    Args:
        url: The client notification tier WebSocket URL, for example
            ``wss://nats.mxraven.com``. Only the ``ws`` and ``wss`` schemes are
            accepted.
        username: The listener API key prefix, for example
            ``mxr_tx_ab12cd34ef56``.
        secret: The listener API key secret. Treat it as a secret and do not
            log it.
        listener: The listener whose events are consumed. It must not contain
            ``.``, ``*``, ``>``, ``/``, a backslash, or whitespace.
        consumer: Overrides the durable consumer name. Defaults to
            ``notify-<listener>``.
        connect_timeout: The connection deadline in seconds. Defaults to
            ``10.0``.
        tls: An optional TLS context for ``wss://`` connections, for example to
            trust a private certificate authority.
        on_error: Receives non-fatal connection and decode errors. It runs on
            the worker thread and must return promptly. When omitted, an
            undecodable event stops consumption with a `DecodeError`.

    Raises:
        ValueError: An option is missing or invalid.
    """

    def __init__(
        self,
        *,
        url: str,
        username: str,
        secret: str,
        listener: str,
        consumer: str | None = None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        tls: ssl.SSLContext | None = None,
        on_error: ErrorHandler | None = None,
    ) -> None:
        """Validate the options. The client is not connected until `connect`."""
        self._options = validate_options(
            url=url,
            username=username,
            secret=secret,
            listener=listener,
            consumer=consumer,
            connect_timeout=connect_timeout,
        )
        self._tls = tls
        self._on_error = on_error
        self._runner = LoopRunner("mxraven-notify")
        self._client: _AsyncNotificationClient | None = None
        self._active: Future[None] | None = None
        self._closed = False
        self._lock = threading.Lock()

    @classmethod
    def connect(
        cls,
        *,
        url: str,
        username: str,
        secret: str,
        listener: str,
        consumer: str | None = None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        tls: ssl.SSLContext | None = None,
        on_error: ErrorHandler | None = None,
    ) -> NotificationClient:
        """Connect to the notification tier and return a client.

        The initial connection fails here rather than on the first event. After
        a successful connection the transport reconnects indefinitely when the
        tier is unreachable, and the consumer resumes from its acknowledgement
        floor.

        Args:
            url: The notification tier WebSocket URL (``ws://`` or ``wss://``).
            username: The listener API key prefix.
            secret: The listener API key secret.
            listener: The listener whose events are consumed.
            consumer: An optional durable consumer name override.
            connect_timeout: The connection deadline in seconds.
            tls: An optional TLS context for ``wss://`` connections.
            on_error: An optional non-fatal error handler.

        Returns:
            A connected client.

        Raises:
            ValueError: An option is missing or invalid.
            NotificationError: The connection failed, the API key was rejected,
                or the optional ``notify`` extra is not installed.
        """
        client = cls(
            url=url,
            username=username,
            secret=secret,
            listener=listener,
            consumer=consumer,
            connect_timeout=connect_timeout,
            tls=tls,
            on_error=on_error,
        )
        client._start()
        return client

    def _start(self) -> None:
        # PLC0415: imported on demand so mxraven.notify does not import the
        # asyncio client at package import time.
        from mxraven.aio.notify.client import NotificationClient  # noqa: PLC0415

        self._runner.start()
        try:
            self._client = self._runner.run(
                NotificationClient.connect(
                    url=self._options.url,
                    username=self._options.username,
                    secret=self._options.secret,
                    listener=self._options.listener,
                    consumer=self._options.consumer,
                    connect_timeout=self._options.connect_timeout,
                    tls=self._tls,
                    on_error=self._on_error,
                ),
                timeout=self._options.connect_timeout + 5.0,
            )
        except BaseException:
            with contextlib.suppress(Exception):
                self._runner.stop()
                self._runner.join(5.0)
            raise

    def consume(self, handler: NotificationHandler) -> None:
        """Consume events until stopped, closed, or the handler stops.

        A handler that returns ``None`` acknowledges the event. A handler that
        raises leaves it unacknowledged so the server redelivers it after a
        delay that doubles with each attempt up to 30 seconds. A handler that
        returns ``STOP_CONSUMING`` acknowledges the event and stops. An
        undecodable event is terminated so it cannot poison the stream, and
        either stops consumption with a `DecodeError` or is reported to
        `on_error` and skipped.

        This call blocks until consumption stops. Call :meth:`close` from
        another thread, or return `STOP_CONSUMING` from the handler, to stop it.

        Args:
            handler: Processes one event at a time on a worker thread.

        Raises:
            NotificationError: The client is closed, consumption is already
                running, or consumption failed.
            DecodeError: An event could not be decoded and no error handler is
                configured.
        """
        client = self._require_client()

        async def adapted(event: NotificationEvent) -> NotificationResult:
            return await asyncio.to_thread(handler, event)

        with self._lock:
            if self._active is not None:
                message = "notify: consumption is already running"
                raise NotificationError(message)
            active = self._runner.submit(client.consume(adapted))
            self._active = active
        try:
            active.result()
        finally:
            with self._lock:
                self._active = None

    def events(self) -> NotificationIterator:
        """Return an iterator for manual acknowledgement.

        The caller acknowledges each event with
        :meth:`~NotificationDelivery.ack`, `nak`, or `term`. Stopping iteration
        leaves unacknowledged events for redelivery after the consumer's
        acknowledgement timeout.
        """
        client = self._require_client()
        return NotificationIterator(self._runner, client.events())

    def close(self) -> None:
        """Release the client's connection.

        Active `consume` calls and iterators stop, and events they did not
        acknowledge are redelivered later. Safe to call more than once.
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            active = self._active
        client = self._client
        runner = self._runner
        try:
            if client is not None:
                runner.run(client.close())
            if active is not None:
                # The consuming call's own outcome is not close()'s concern;
                # closing already asked it to stop.
                with contextlib.suppress(Exception):
                    active.result(5.0)
        finally:
            runner.stop()
            runner.join(5.0)
            runner.close()

    def __enter__(self) -> Self:
        """Return this client."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close the client."""
        self.close()

    def _require_client(self) -> _AsyncNotificationClient:
        client = self._client
        if self._closed or client is None:
            message = "notify: client is closed"
            raise NotificationError(message)
        return client


class NotificationDelivery:
    """One event yielded by a :class:`NotificationIterator`.

    Exactly one of `ack`, `nak`, or `term` must be called for the event;
    calling more than one raises `NotificationError`.
    """

    __slots__ = ("_delivery", "_runner")

    def __init__(self, runner: LoopRunner, delivery: _AsyncDelivery) -> None:
        """Wrap an asynchronous delivery for blocking settlement."""
        self._runner = runner
        self._delivery = delivery

    @property
    def event(self) -> NotificationEvent:
        """The decoded notification."""
        return self._delivery.event

    def ack(self) -> None:
        """Acknowledge the event so it is not redelivered."""
        self._runner.run(self._delivery.ack())

    def nak(self, delay: float | None = None) -> None:
        """Negatively acknowledge the event so the server redelivers it.

        Args:
            delay: An optional redelivery delay in seconds.
        """
        self._runner.run(self._delivery.nak(delay))

    def term(self) -> None:
        """Tell the server never to redeliver the event."""
        self._runner.run(self._delivery.term())


class NotificationIterator:
    """An iterator over a listener's notification events.

    Use ``for`` and ``break`` when finished; the iterator stops pulling and
    leaves unacknowledged events for redelivery. Iteration also stops when the
    client's connection closes.
    """

    __slots__ = ("_iterator", "_runner")

    def __init__(self, runner: LoopRunner, iterator: _AsyncIterator) -> None:
        """Wrap an asynchronous iterator for blocking iteration."""
        self._runner = runner
        self._iterator = iterator

    def __iter__(self) -> Self:
        """Return this iterator."""
        return self

    def __next__(self) -> NotificationDelivery:
        """Yield the next delivery, or stop iteration."""
        try:
            delivery = self._runner.run(self._iterator.__anext__())
        except StopAsyncIteration:
            raise StopIteration from None
        return NotificationDelivery(self._runner, delivery)

    def close(self) -> None:
        """Stop iterating. Safe to call more than once."""
        self._runner.run(self._iterator.aclose())
