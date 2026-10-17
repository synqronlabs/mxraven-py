"""NATS JetStream transport for the client notification consumer.

This module is the only place that imports :mod:`nats`, which is an optional
dependency supplied by the ``mxraven[notify]`` extra. It is imported lazily by
the public clients so that importing :mod:`mxraven.notify` does not require the
extra.
"""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

import nats

from mxraven._internal.notify.config import extra_required_message
from mxraven.errors import NotificationError

if TYPE_CHECKING:
    import ssl
    from collections.abc import Awaitable, Callable

    from nats.aio.client import Client as NatsClient
    from nats.aio.msg import Msg
    from nats.js.client import JetStreamContext

__all__ = [
    "CLIENT_NAME",
    "STREAM_NAME",
    "Message",
    "Session",
    "Subscription",
    "open_session",
]

STREAM_NAME = "CLIENT_NOTIFY"
CLIENT_NAME = "mxraven-notify"
_INBOX_PREFIX = "_INBOX."


class Message:
    """One pulled JetStream message with its acknowledgement operations."""

    __slots__ = ("_msg",)

    def __init__(self, msg: Msg) -> None:
        """Wrap a NATS message."""
        self._msg = msg

    @property
    def subject(self) -> str:
        """The subject the message was published to."""
        return self._msg.subject

    @property
    def data(self) -> bytes:
        """The raw message body."""
        return self._msg.data

    @property
    def num_delivered(self) -> int:
        """How many times the server has delivered this message."""
        try:
            return int(self._msg.metadata.num_delivered)
        except Exception:  # metadata is absent for non-JetStream messages
            return 1

    async def ack(self) -> None:
        """Acknowledge the message."""
        await self._msg.ack()

    async def nak(self, delay: float | None = None) -> None:
        """Negatively acknowledge the message, optionally after `delay` seconds."""
        await self._msg.nak(delay=delay)

    async def term(self) -> None:
        """Terminate the message so the server never redelivers it."""
        await self._msg.term()


class Subscription:
    """A pull subscription bound to one durable consumer."""

    __slots__ = ("_sub",)

    def __init__(self, sub: JetStreamContext.PullSubscription) -> None:
        """Wrap a pull subscription."""
        self._sub = sub

    async def fetch(self, timeout: float) -> list[Message]:
        """Fetch at most one message, waiting up to `timeout` seconds.

        Raises:
            TimeoutError: No message arrived before `timeout` elapsed.
        """
        messages = await self._sub.fetch(1, timeout=timeout)
        return [Message(message) for message in messages]

    async def close(self) -> None:
        """Remove interest in the pull inbox. Safe to call more than once."""
        try:
            await self._sub.unsubscribe()
        except Exception:  # a closed connection already removed it
            return


class Session:
    """Owns one NATS connection to the client notification tier."""

    __slots__ = ("_closed", "_consumer", "_js", "_nc")

    def __init__(self, nc: NatsClient, consumer: str) -> None:
        """Wrap a connected NATS client and the listener's consumer name."""
        self._nc = nc
        self._js = nc.jetstream()
        self._consumer = consumer
        self._closed = False

    @property
    def is_closed(self) -> bool:
        """Whether the session or its connection has closed."""
        return self._closed or self._nc.is_closed

    async def pull(self) -> Subscription:
        """Bind a new pull subscription to the listener's durable consumer."""
        sub = await self._js.pull_subscribe_bind(stream=STREAM_NAME, consumer=self._consumer)
        return Subscription(sub)

    async def verify(self) -> None:
        """Resolve the listener's durable consumer, failing if it is absent.

        The control plane creates the consumer when notifications are enabled,
        so a missing consumer means the setting is off or the client bound the
        wrong name.

        Raises:
            NotificationError: The consumer could not be resolved.
        """
        try:
            await self._js.consumer_info(STREAM_NAME, self._consumer)
        except Exception as error:
            detail = f"notify: resolve consumer {self._consumer!r}: {error}"
            raise NotificationError(detail) from error

    async def close(self) -> None:
        """Close the connection. Safe to call more than once."""
        if self._closed:
            return
        self._closed = True
        await self._nc.close()


async def open_session(
    *,
    url: str,
    username: str,
    secret: str,
    listener: str,
    consumer: str,
    connect_timeout: int,
    tls: ssl.SSLContext | None,
    on_error: Callable[[Exception], Awaitable[None]],
) -> Session:
    """Connect to the notification tier and return a session.

    Args:
        url: The ``ws://`` or ``wss://`` notification tier URL.
        username: The listener API key prefix.
        secret: The listener API key secret.
        listener: The listener identifier, used for the inbox prefix.
        consumer: The durable consumer name to bind.
        connect_timeout: The connection timeout in whole seconds.
        tls: An optional TLS context for ``wss://`` connections.
        on_error: An asynchronous callback for non-fatal connection errors.

    Returns:
        A connected session.

    Raises:
        NotificationError: The connection could not be established, including
            when the optional ``aiohttp`` WebSocket transport is unavailable.
    """
    # The transport is an optional dependency; a missing WebSocket provider
    # surfaces as an ImportError here.
    try:
        # nats-py passes a float connect timeout to aiohttp, which aiohttp 3.13+
        # deprecates. Ignore only that message so a caller that treats warnings
        # as errors can still connect; the transport is not otherwise affected.
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r"parameter 'timeout' of type 'float' is deprecated.*",
                category=DeprecationWarning,
            )
            nc = await nats.connect(
                servers=url,
                user=username,
                password=secret,
                name=CLIENT_NAME,
                inbox_prefix=f"{_INBOX_PREFIX}{listener}",
                connect_timeout=connect_timeout,
                allow_reconnect=True,
                max_reconnect_attempts=-1,
                error_cb=on_error,
                tls=tls,
            )
    except ImportError as error:
        raise NotificationError(extra_required_message()) from error
    return Session(nc, consumer)
