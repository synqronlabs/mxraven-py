# Notifications

Consume the durable mail events a listener publishes over NATS JetStream,
using the same API key as SMTP submission. Notifications are off by default;
enable them for the listener with the mxRaven administrative API first, which
creates the durable consumer the client reads from.

This surface needs the optional `notify` extra, which installs the NATS
transport:

```sh
uv add "mxraven[notify]"
# or
pip install "mxraven[notify]"
```

The rest of the SDK stays dependency-free.

## Events

Each event is one of two kinds:

- `accepted` — a message the listener accepted after its terminal action was
  resolved. The payload is the same body mxRaven posts to a webhook endpoint,
  decoded into `mxraven.webhook.InboundEmail`. An MTA listener's accepted event
  carries raw-message access that `InboundEmail.raw_email.fetch()` downloads; a
  submission listener's leaves it empty.
- `attempt` — one delivery attempt for an accepted message on an egress
  channel (`smtp`, `s3`, or `webhook`), decoded into `mxraven.notify.AttemptEvent`.
  Every attempt carries a `correlation_id` pointing at the `task_id` of the
  accepted event for its message.

Both kinds carry `message_ref`, the mxRaven message reference. For an outbound
listener it is the value a submission reply reports as `message_ref=`, so it
joins an event back to the send that produced it: `send()` returns the same value
as `SendResult.message_ref`. It is the only identifier in an event the caller
already holds, and it groups the attempts of a fan-out that otherwise have
distinct task ids.

```python
from mxraven.notify import NotificationEvent, NotificationKind
```

## Synchronous client

```python
import os

from mxraven.notify import NotificationClient

with NotificationClient.connect(
    url="wss://nats.mxraven.com",
    username="mxr_tx_ab12cd34ef56",
    secret=os.environ["MXRAVEN_SECRET"],
    listener=listener_id,
) as client:
    for delivery in client.events():
        event = delivery.event
        if event.kind is NotificationKind.ACCEPTED:
            handle_accepted(event.accepted)
        else:
            handle_attempt(event.attempt)
        delivery.ack()
```

`consume(handler)` dispatches each event to a handler and manages
acknowledgement:

- a handler that returns normally acknowledges the event;
- a handler that raises leaves the event unacknowledged, and the server
  redelivers it with exponential backoff capped at 30 seconds;
- a handler that returns `STOP_CONSUMING` acknowledges the event and stops
  consumption;
- an event the SDK cannot decode is terminated so it cannot poison the stream.
  Without an `on_error` handler, consumption stops with a `DecodeError`;
  with one, the error is reported and the next event is read.

Handlers run on a worker thread. To stop a blocking `consume` call, call
`close()` from another thread or return `STOP_CONSUMING`.

## Asynchronous client

The asyncio counterpart lives in `mxraven.aio.notify` with the same payloads and
error surface:

```python
import asyncio

from mxraven.aio.notify import NotificationClient


async def main() -> None:
    client = await NotificationClient.connect(
        url="wss://nats.mxraven.com",
        username="mxr_tx_ab12cd34ef56",
        secret=os.environ["MXRAVEN_SECRET"],
        listener=listener_id,
    )
    try:
        async for delivery in client.events():
            await handle(delivery.event)
            await delivery.ack()
    finally:
        await client.close()


asyncio.run(main())
```

A handler may be a plain callable or a coroutine function. Cancelling the
`consume` task is a normal stop.

## Retention and delivery

The stream keeps events for 24 hours from creation regardless of
acknowledgement. Acknowledging stops redelivery; it does not remove the event.
A client that is offline longer than the retention window cannot recover the
gap. Connections that share one API key share one consumer, so events are
distributed between them rather than duplicated to each.
