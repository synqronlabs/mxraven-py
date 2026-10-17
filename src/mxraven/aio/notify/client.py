"""Asynchronous mxRaven client notification consumer.

Consumes the durable mail events a listener publishes over NATS JetStream,
using the same API key as SMTP submission. The transport requires the optional
``notify`` extra: install ``mxraven[notify]``.
"""

from __future__ import annotations

import contextlib
import inspect
from typing import TYPE_CHECKING, Self, TypeAlias

from mxraven._internal.notify.config import (
    DEFAULT_CONNECT_TIMEOUT,
    PULL_TIMEOUT,
    load_open_session,
    retry_delay,
    validate_options,
)
from mxraven.errors import DecodeError, NotificationError
from mxraven.notify.event import NotificationEvent, decode_notification
from mxraven.notify.handler import STOP_CONSUMING

if TYPE_CHECKING:
    import ssl
    from collections.abc import Awaitable, Callable

    from mxraven._internal.notify.session import Message, Session, Subscription
    from mxraven.notify.handler import NotificationResult

__all__ = ["NotificationClient", "NotificationDelivery", "NotificationIterator"]

AsyncHandlerResult: TypeAlias = "NotificationResult | Awaitable[NotificationResult]"
NotificationHandler: TypeAlias = "Callable[[NotificationEvent], AsyncHandlerResult]"
ErrorHandler: TypeAlias = "Callable[[NotificationError], Awaitable[None] | None]"


class NotificationClient:
    """Consumes one listener's client notification events asynchronously.

    Notifications are off by default; enable them for the listener with the
    mxRaven administrative API first, which creates the durable consumer this
    client reads from. The connection authenticates with the listener's
    ordinary API key and is granted access only to that listener's consumer.

    A client is safe for concurrent use. A `consume` call and an `events`
    iterator share the durable consumer with any other client using the same
    API key: events are distributed between them, not duplicated to each.

    Use :meth:`connect` to build a connected client; do not construct one
    directly. A client is an async context manager.

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
        on_error: Receives non-fatal connection and decode errors. When omitted,
            an undecodable event stops consumption with a `DecodeError`.

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
        self._session: Session | None = None
        self._closed = False

    @classmethod
    async def connect(
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
        await client._connect()
        return client

    async def _connect(self) -> None:
        open_session = load_open_session()
        session = await open_session(
            url=self._options.url,
            username=self._options.username,
            secret=self._options.secret,
            listener=self._options.listener,
            consumer=self._options.consumer,
            connect_timeout=int(self._options.connect_timeout),
            tls=self._tls,
            on_error=self._connection_error,
        )
        try:
            await session.verify()
        except Exception:
            # Preserve the resolution failure; a failing close must not mask it.
            with contextlib.suppress(Exception):
                await session.close()
            raise
        self._session = session

    async def consume(self, handler: NotificationHandler) -> None:
        """Consume events until stopped, closed, or the handler stops.

        A handler that returns ``None`` acknowledges the event. A handler that
        raises leaves it unacknowledged so the server redelivers it after a
        delay that doubles with each attempt up to 30 seconds. A handler that
        returns ``STOP_CONSUMING`` acknowledges the event and stops. An
        undecodable event is terminated so it cannot poison the stream, and
        either stops consumption with a `DecodeError` or is reported to
        `on_error` and skipped.

        Cancellation is a normal stop. A permanently lost consumer or
        connection raises `NotificationError`.

        Args:
            handler: Processes one event at a time. It may be a coroutine
                function or a plain callable.

        Raises:
            NotificationError: The client is closed, or consumption failed.
            DecodeError: An event could not be decoded and no error handler is
                configured.
        """
        session = self._require_session()
        subscription = await session.pull()
        try:
            while True:
                try:
                    messages = await subscription.fetch(PULL_TIMEOUT)
                except TimeoutError:
                    if session.is_closed:
                        return
                    continue
                except Exception as error:
                    if session.is_closed:
                        return
                    operation = "notify: consume"
                    raise _wrap(operation, error) from error
                for message in messages:
                    if await self._dispatch(message, handler):
                        return
        finally:
            await subscription.close()

    def events(self) -> NotificationIterator:
        """Return an async iterator for manual acknowledgement.

        The caller acknowledges each event with
        :meth:`~NotificationDelivery.ack`, `nak`, or `term`. Stopping iteration
        leaves unacknowledged events for redelivery after the consumer's
        acknowledgement timeout.
        """
        return NotificationIterator(self)

    async def close(self) -> None:
        """Release the client's connection.

        Active `consume` calls and iterators stop, and events they did not
        acknowledge are redelivered later. Safe to call more than once.
        """
        if self._closed:
            return
        self._closed = True
        session = self._session
        if session is not None:
            await session.close()

    async def __aenter__(self) -> Self:
        """Return this client."""
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        """Close the client."""
        await self.close()

    async def _dispatch(self, message: Message, handler: NotificationHandler) -> bool:
        try:
            event = decode_notification(message.subject, message.data)
        except DecodeError as error:
            await self._settle("notify: terminate event", message.term())
            if self._on_error is None:
                raise
            await self._report(error)
            return False
        try:
            result = handler(event)
            if inspect.isawaitable(result):
                result = await result
        except Exception:
            await self._settle(
                "notify: retry event", message.nak(retry_delay(message.num_delivered))
            )
            return False
        await self._settle("notify: acknowledge event", message.ack())
        return result is STOP_CONSUMING

    def _require_session(self) -> Session:
        session = self._session
        if session is None:
            message = "notify: client is not connected"
            raise NotificationError(message)
        if self._closed or session.is_closed:
            message = "notify: client is closed"
            raise NotificationError(message)
        return session

    async def _settle(self, operation: str, awaitable: Awaitable[None]) -> None:
        try:
            await awaitable
        except Exception as error:
            await self._report(_wrap(operation, error))

    async def _report(self, error: NotificationError) -> None:
        handler = self._on_error
        if handler is None:
            return
        try:
            result = handler(error)
            if inspect.isawaitable(result):
                await result
        except Exception:
            # The callback contract says not to throw; keeping the transport
            # usable is more valuable than tearing it down from an error report.
            return

    async def _connection_error(self, error: Exception) -> None:
        await self._report(_wrap("notify: connection", error))


class NotificationDelivery:
    """One event yielded by a :class:`NotificationIterator`.

    Exactly one of `ack`, `nak`, or `term` must be called for the event; calling
    more than one raises `NotificationError`. An event that is never settled is
    redelivered after the consumer's acknowledgement timeout.
    """

    __slots__ = ("_client", "_event", "_message", "_settled")

    def __init__(
        self, client: NotificationClient, message: Message, event: NotificationEvent
    ) -> None:
        """Wrap a decoded event for manual settlement."""
        self._client = client
        self._message = message
        self._event = event
        self._settled = False

    @property
    def event(self) -> NotificationEvent:
        """The decoded notification."""
        return self._event

    async def ack(self) -> None:
        """Acknowledge the event so it is not redelivered.

        Raises:
            NotificationError: The event was already settled.
        """
        self._claim()
        await self._client._settle("notify: acknowledge event", self._message.ack())

    async def nak(self, delay: float | None = None) -> None:
        """Negatively acknowledge the event so the server redelivers it.

        Args:
            delay: An optional redelivery delay in seconds. When omitted or
                zero, the server redelivers immediately.

        Raises:
            ValueError: `delay` is negative.
            NotificationError: The event was already settled.
        """
        if delay is not None and delay < 0:
            message = f"notify: invalid nak delay {delay!r}"
            raise ValueError(message)
        self._claim()
        resolved = delay if delay else None
        await self._client._settle("notify: negate event", self._message.nak(resolved))

    async def term(self) -> None:
        """Tell the server never to redeliver the event.

        Raises:
            NotificationError: The event was already settled.
        """
        self._claim()
        await self._client._settle("notify: terminate event", self._message.term())

    def _claim(self) -> None:
        if self._settled:
            message = "notify: event is already settled"
            raise NotificationError(message)
        self._settled = True


class NotificationIterator:
    """An async iterator over a listener's notification events.

    Use ``async for`` and ``break`` when finished; the iterator stops pulling
    and leaves unacknowledged events for redelivery. Iteration also stops when
    the client's connection closes.
    """

    __slots__ = ("_client", "_closed", "_subscription")

    def __init__(self, client: NotificationClient) -> None:
        """Build an iterator over `client`."""
        self._client = client
        self._subscription: Subscription | None = None
        self._closed = False

    def __aiter__(self) -> Self:
        """Return this iterator."""
        return self

    async def __anext__(self) -> NotificationDelivery:
        """Yield the next delivery, or stop iteration."""
        if self._closed:
            raise StopAsyncIteration
        session = self._client._require_session()
        subscription = await self._subscription_for(session)
        while True:
            try:
                messages = await subscription.fetch(PULL_TIMEOUT)
            except TimeoutError:
                if session.is_closed:
                    raise StopAsyncIteration from None
                continue
            except Exception as error:
                if session.is_closed:
                    raise StopAsyncIteration from None
                operation = "notify: iterate events"
                raise _wrap(operation, error) from error
            if not messages:
                continue
            message = messages[0]
            try:
                event = decode_notification(message.subject, message.data)
            except DecodeError as error:
                await self._client._settle("notify: terminate event", message.term())
                if self._client._on_error is None:
                    await self.aclose()
                    raise
                await self._client._report(error)
                continue
            return NotificationDelivery(self._client, message, event)

    async def aclose(self) -> None:
        """Stop iterating. Safe to call more than once."""
        if self._closed:
            return
        self._closed = True
        subscription = self._subscription
        if subscription is not None:
            await subscription.close()

    async def _subscription_for(self, session: Session) -> Subscription:
        if self._subscription is None:
            self._subscription = await session.pull()
        return self._subscription


def _wrap(operation: str, error: BaseException) -> NotificationError:
    wrapped = NotificationError(f"{operation}: {error}")
    wrapped.__cause__ = error
    return wrapped
