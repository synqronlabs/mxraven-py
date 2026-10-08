# SMTP submission

The root `mxraven` package submits mail to the mxRaven SMTP submission service
on port 587. The service requires STARTTLS and SMTP AUTH.

## Composing messages

`Message` is a mutable, chainable builder. A message may be sent more than once.

```python
from mxraven import Message

message = (
    Message()
    .from_("Acme <noreply@acme.example>")
    .reply_to("support@acme.example")
    .to("customer@example.com")
    .cc("accounting@acme.example")
    .bcc("audit@acme.example")
    .subject("Invoice 1842")
    .header("X-Campaign", "september")
    .text("Your invoice is attached.")
    .html("<p>Your invoice is attached.</p>")
    .attach_file("invoice.pdf", pdf_bytes)
)
```

- Setting both `text` and `html` produces a `multipart/alternative` body.
- `attach_file` adds a file with an `application/octet-stream` content type; use
  `attach` to set a content type, or `attach_inline` for `cid:` references.
- `bcc` adds envelope recipients without a visible `Bcc` header.
- `null_sender` uses a null reverse-path for bounce and auto-generated mail
  while keeping the `From` header.
- Bodies whose lines would exceed the RFC 5322 limit of 998 octets are encoded
  as `quoted-printable` automatically; otherwise ASCII bodies use `7bit` and
  non-ASCII bodies use `8bit`.

## Requiring TLS and bounding delivery time

`Message.require_tls` requests that every hop delivering the message use TLS
([RFC 8689](https://www.rfc-editor.org/rfc/rfc8689)). The session must already be
encrypted and the service must advertise `REQUIRETLS`, otherwise `send` raises
`SMTPError`.

`Message.deliver_by` asks the service to deliver within a bounded window
([RFC 2852](https://www.rfc-editor.org/rfc/rfc2852)). `DeliveryByMode.RETURN`
asks for the message to be returned when the deadline passes;
`DeliveryByMode.NOTIFY` asks for a delayed notification instead. The service
must advertise `DELIVERBY`, otherwise `send` raises `SMTPCapabilityError`.

```python
from mxraven import DeliveryBy, DeliveryByMode

message = (
    Message()
    .from_("noreply@acme.example")
    .to("customer@example.com")
    .require_tls()
    .deliver_by(DeliveryBy(600, DeliveryByMode.RETURN))
    .text("Time-sensitive notice.")
)
```

Both options are also available on `Envelope` for `send_raw`.

## Sending raw messages

Use `send_raw` to stream an already serialized RFC 5322 message with an explicit
envelope. The message is not parsed, so the caller is responsible for RFC 5322
correctness.

```python
client.send_raw(
    {"from": "bounce@acme.example", "to": ["customer@example.com"]},
    raw_message_bytes,
)
```

## Results

`send` returns a `Result` with the mxRaven `message_ref`, the final SMTP reply,
and per-recipient acceptance.

## Connection pooling

A `Client` keeps a bounded pool of authenticated connections and is safe for
concurrent use. Always call `close` to release them.
