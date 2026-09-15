# Webhooks

mxRaven signs every webhook delivery with HMAC-SHA256 over a canonical request
string and sends the signature in the `X-MxRaven-*` headers.

## Verify and decode

```python
from mxraven.webhook import Verifier

verifier = Verifier(secret=signing_secret)
event = verifier.verify_and_decode(request)
```

## Canonical string

Fields are joined by newlines (`\n`):

```text
{timestamp}
{webhook_id}
{METHOD}
{lowercase host, including port when present}
{escaped path plus raw query, or "/"}
{lowercase hex SHA-256 of the exact raw body}
```

| Header | Meaning |
| --- | --- |
| `X-MxRaven-Webhook-ID` | The delivery (task) ID. Stable across retries. |
| `X-MxRaven-Timestamp` | Unix signing time, in seconds. |
| `X-MxRaven-Signature` | `sha256=<lowercase hex HMAC>`. |
| `X-MxRaven-Signature-Kid` | Signing key ID. |

The signing secret is used as literal key bytes; do not base64-decode it. The
timestamp is checked against the current time with a five-minute tolerance by
default.

## Events

Exactly one payload is populated, matching `event.type`:

- `inbound_email` — a `DELIVER_WEBHOOK` delivery carrying a complete inbound
  message.
- `delivery_status` — an SMTP `NOTIFY_WEBHOOK` delivery status.
- `s3_egress_status` — an object-storage `NOTIFY_WEBHOOK` delivery status.
