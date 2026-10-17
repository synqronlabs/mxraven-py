"""An in-process NATS-over-WebSocket server for notification tests.

It implements the small subset of the NATS protocol and JetStream API the
notification client uses: the INFO/CONNECT handshake, subscriptions,
request-reply, one pre-created pull consumer, and message acknowledgement. It
is intentionally not a general-purpose NATS server.

Adapted from ``tmp/mxraven-js/src/notify/__fixtures__/fake-nats.ts``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
from collections import deque
from typing import Self

from aiohttp import WSMsgType, web

__all__ = ["FakeAck", "FakeNatsEvent", "FakeNatsServer", "ThreadedFakeNatsServer"]


class FakeNatsEvent:
    """One event the fake server can deliver."""

    __slots__ = ("body", "subject")

    def __init__(self, subject: str, body: bytes) -> None:
        """Create an event."""
        self.subject = subject
        self.body = body


class FakeAck:
    """A payload the client published to an acknowledgement subject."""

    __slots__ = ("payload", "subject")

    def __init__(self, subject: str, payload: str) -> None:
        """Create an acknowledgement record."""
        self.subject = subject
        self.payload = payload


class FakeNatsServer:
    """A minimal NATS-over-WebSocket server bound to loopback."""

    def __init__(
        self,
        *,
        consumer: str = "notify-listener-1",
        stream: str = "CLIENT_NOTIFY",
        consumer_not_found: bool = False,
    ) -> None:
        """Create a stopped server."""
        self._consumer = consumer
        self._stream = stream
        self._consumer_not_found = consumer_not_found
        self._app = web.Application()
        self._app.router.add_route("GET", "/", self._handle)
        self._runner = web.AppRunner(self._app)
        self._site: web.TCPSite | None = None
        self._ws: web.WebSocketResponse | None = None
        self._subscriptions: dict[str, str] = {}
        self._pulls: deque[str] = deque()
        self._events: deque[FakeNatsEvent] = deque()
        self._acks: list[FakeAck] = []
        self._buffer = b""
        self._delivery = 0
        self._flush_task: asyncio.Future[None] | None = None
        self.connect_options: dict[str, object] | None = None

    @property
    def url(self) -> str:
        """The ``ws://`` URL a client connects to."""
        if self._site is None:  # pragma: no cover - only before start
            message = "fake NATS server is not running"
            raise RuntimeError(message)
        sockets = self._site._server.sockets  # type: ignore[union-attr]
        port = sockets[0].getsockname()[1]
        return f"ws://127.0.0.1:{port}"

    @property
    def acks(self) -> list[FakeAck]:
        """Every acknowledgement payload the client published."""
        return self._acks

    async def start(self) -> FakeNatsServer:
        """Start the server and return it."""
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await self._site.start()
        return self

    async def close(self) -> None:
        """Close the server and any open connection."""
        if self._ws is not None:
            await self._ws.close()
        await self._runner.cleanup()

    def enqueue(self, event: FakeNatsEvent) -> None:
        """Queue one event for the next pull request."""
        self._events.append(event)
        self._flush_task = asyncio.ensure_future(self._flush())

    async def wait_for_ack(self, payload: str, *, timeout: float = 5.0) -> None:
        """Wait until an acknowledgement with `payload` has been recorded."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if any(ack.payload == payload for ack in self._acks):
                return
            await asyncio.sleep(0.01)
        message = f"no acknowledgement {payload!r} within {timeout}s"
        raise AssertionError(message)

    async def _handle(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._ws = ws
        await self._send_text(f"INFO {json.dumps(self._server_info())}\r\n")
        async for message in ws:
            if message.type is WSMsgType.BINARY or message.type is WSMsgType.TEXT:
                self._buffer += bytes(message.data)
            else:
                continue
            await self._parse()
        return ws

    def _server_info(self) -> dict[str, object]:
        return {
            "server_id": "fake",
            "server_name": "fake",
            "version": "2.11.0",
            "proto": 1,
            "go": "go1.26",
            "host": "127.0.0.1",
            "port": 4222,
            "headers": True,
            "max_payload": 1 << 20,
            "jetstream": True,
        }

    async def _parse(self) -> None:
        while True:
            line_end = self._buffer.find(b"\r\n")
            if line_end < 0:
                return
            line = self._buffer[:line_end].decode()
            if line.startswith("PUB ") or line.startswith("HPUB "):
                consumed = await self._parse_publish(line, line_end)
                if consumed is None:
                    return
                continue
            self._buffer = self._buffer[line_end + 2 :]
            await self._handle_control(line)

    async def _parse_publish(self, line: str, line_end: int) -> int | None:
        parts = line.split()
        is_headers = parts[0] == "HPUB"
        subject = parts[1] if len(parts) > 1 else ""
        reply: str | None = None
        total = 0
        if is_headers:
            try:
                total = int(parts[-1])
            except ValueError:
                return None
        elif len(parts) == 3:
            total = int(parts[2])
        elif len(parts) == 4:
            reply = parts[2]
            total = int(parts[3])
        start = line_end + 2
        end = start + total
        if len(self._buffer) < end + 2:
            return None
        payload = self._buffer[start:end].decode()
        self._buffer = self._buffer[end + 2 :]
        await self._handle_publish(subject, reply, payload)
        return end + 2

    async def _handle_control(self, line: str) -> None:
        parts = line.split()
        if not parts:
            return
        command = parts[0]
        if command == "CONNECT":
            self.connect_options = json.loads(line[len("CONNECT ") :])
        elif command == "PING":
            await self._send_text("PONG\r\n")
        elif command == "SUB":
            subject = parts[1] if len(parts) > 1 else ""
            sid = parts[-1] if len(parts) > 1 else ""
            self._subscriptions[sid] = subject
        elif command == "UNSUB":
            sid = parts[1] if len(parts) > 1 else ""
            self._subscriptions.pop(sid, None)

    async def _handle_publish(self, subject: str, reply: str | None, payload: str) -> None:
        if subject.startswith("$JS.API.CONSUMER.INFO."):
            if reply is not None:
                await self._respond_consumer_info(reply)
            return
        if subject.startswith("$JS.API.CONSUMER.MSG.NEXT."):
            if reply is not None:
                self._pulls.append(reply)
                await self._flush()
            return
        if subject.startswith("$JS.ACK."):
            # nats-py acknowledges with an empty payload; the JS client sends
            # an explicit +ACK. Normalize so tests read the same either way.
            self._acks.append(FakeAck(subject, payload or "+ACK"))

    async def _respond_consumer_info(self, reply: str) -> None:
        sid = self._find_sid(reply)
        if sid is None:
            return
        body: dict[str, object]
        if self._consumer_not_found:
            body = {
                "error": {"code": 404, "description": "consumer not found"},
                "type": "io.nats.jetstream.api.v1.consumer_info_response",
            }
        else:
            body = {
                "type": "io.nats.jetstream.api.v1.consumer_info_response",
                "stream_name": self._stream,
                "name": self._consumer,
                "created": "2026-01-01T00:00:00Z",
                "config": {
                    "durable_name": self._consumer,
                    "name": self._consumer,
                    "deliver_policy": "all",
                    "ack_policy": "explicit",
                    "replay_policy": "instant",
                    "ack_wait": 30_000_000_000,
                    "max_ack_pending": 256,
                    "max_deliver": -1,
                },
                "delivered": {"consumer_seq": 0, "stream_seq": 0},
                "ack_floor": {"consumer_seq": 0, "stream_seq": 0},
                "num_ack_pending": 0,
                "num_redelivered": 0,
                "num_waiting": 0,
                "num_pending": 0,
            }
        encoded = json.dumps(body).encode()
        header = f"MSG {reply} {sid} {len(encoded)}\r\n".encode()
        await self._send_bytes(header + encoded + b"\r\n")

    async def _flush(self) -> None:
        while self._pulls and self._events:
            reply = self._pulls.popleft()
            event = self._events.popleft()
            sid = self._find_sid(reply)
            if sid is None:
                continue
            self._delivery += 1
            ack = (
                f"$JS.ACK.{self._stream}.{self._consumer}."
                f"{self._delivery}.{self._delivery}.{self._delivery}.0.0"
            )
            header = f"MSG {event.subject} {sid} {ack} {len(event.body)}\r\n".encode()
            await self._send_bytes(header + event.body + b"\r\n")

    def _find_sid(self, subject: str) -> str | None:
        for sid, subscribed in self._subscriptions.items():
            if subscribed == subject or _matches(subscribed, subject):
                return sid
        return None

    async def _send_text(self, data: str) -> None:
        await self._send_bytes(data.encode())

    async def _send_bytes(self, data: bytes) -> None:
        if self._ws is not None and not self._ws.closed:
            await self._ws.send_bytes(data)


def _matches(pattern: str, subject: str) -> bool:
    """Report whether a NATS subject matches a subscription pattern."""
    wanted = pattern.split(".")
    actual = subject.split(".")
    for index, token in enumerate(wanted):
        if token == ">":
            return True
        if token != "*" and (index >= len(actual) or token != actual[index]):
            return False
    return len(wanted) == len(actual)


class ThreadedFakeNatsServer:
    """Runs a :class:`FakeNatsServer` on a private event loop thread.

    The synchronous client blocks the calling thread, so its test server must
    run its own loop. Use as a context manager.
    """

    def __init__(self, **kwargs: object) -> None:
        """Create a stopped threaded server."""
        self._kwargs = kwargs
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="fake-nats", daemon=True)
        self._ready = threading.Event()
        self._server: FakeNatsServer | None = None

    def __enter__(self) -> Self:
        """Start the loop thread and server."""
        self._thread.start()
        self._ready.wait()
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Stop the server and its loop."""
        self.stop()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        server = FakeNatsServer(**self._kwargs)  # type: ignore[arg-type]
        self._server = self._loop.run_until_complete(server.start())
        self._ready.set()
        self._loop.run_forever()

    @property
    def url(self) -> str:
        """The ``ws://`` URL a client connects to."""
        return self._require().url

    @property
    def acks(self) -> list[FakeAck]:
        """Every acknowledgement the client published."""
        return self._require().acks

    @property
    def connect_options(self) -> dict[str, object] | None:
        """The parsed CONNECT payload, once the client has sent it."""
        return self._require().connect_options

    def enqueue(self, event: FakeNatsEvent) -> None:
        """Queue one event for the next pull request."""
        server = self._require()
        self._loop.call_soon_threadsafe(server.enqueue, event)

    def wait_for_ack(self, payload: str, *, timeout: float = 5.0) -> None:
        """Block until an acknowledgement with `payload` has been recorded."""
        server = self._require()
        future = asyncio.run_coroutine_threadsafe(
            server.wait_for_ack(payload, timeout=timeout), self._loop
        )
        future.result(timeout + 1.0)

    def stop(self) -> None:
        """Stop the server and its loop thread."""
        server = self._server
        if server is None:
            return
        with contextlib.suppress(Exception):
            asyncio.run_coroutine_threadsafe(server.close(), self._loop).result(5.0)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(5.0)
        self._loop.close()
        self._server = None

    def _require(self) -> FakeNatsServer:
        if self._server is None:
            message = "threaded fake NATS server is not running"
            raise RuntimeError(message)
        return self._server
