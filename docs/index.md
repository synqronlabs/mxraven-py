# mxRaven Python SDK

The official Python SDK for the mxRaven **mail-facing** runtime surfaces.

It covers three integration points:

- **SMTP submission** (`mxraven`) — submit mail through the mxRaven submission
  service.
- **Webhooks** (`mxraven.webhook`) — verify and decode `DELIVER_WEBHOOK` and
  `NOTIFY_WEBHOOK` deliveries.
- **Feedback** (`mxraven.feedback`) — train the spam filter and perform RFC 8058
  one-click unsubscribes.

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

See [SMTP submission](submission.md), [Webhooks](webhooks.md), and
[Feedback](feedback.md) for usage, or the [API reference](reference/index.md)
for the complete surface.
