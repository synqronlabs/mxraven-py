# mxRaven Python SDK

The official Python SDK for the mxRaven **mail-facing** runtime surfaces.

It covers three integration points:

- **SMTP submission** (`mxraven`) — submit mail through the mxRaven submission
  service.
- **Webhooks** (`mxraven.webhook`) — verify and decode `DELIVER_WEBHOOK` and
  `NOTIFY_WEBHOOK` deliveries.
- **MIME parsing** (`mxraven.mime`) — turn the raw message behind an
  inbound-email payload into headers, bodies, and decoded attachments.
- **Feedback** (`mxraven.feedback`) — train the spam filter and perform RFC 8058
  one-click unsubscribes.
- **Notifications** (`mxraven.notify`) — consume a listener's accepted-message
  and delivery-attempt events over NATS JetStream.

Control-plane administration is out of scope and lives in the separate
control-plane SDK.

!!! note "Early development"
    The public API is not yet stable. Pin the version you depend on.

## Install

```sh
uv add mxraven
# or
pip install mxraven
```

See [SMTP submission](submission.md), [Webhooks](webhooks.md),
[Feedback](feedback.md), and [Notifications](notifications.md) for usage, or the
[API reference](reference/index.md) for the complete surface.
