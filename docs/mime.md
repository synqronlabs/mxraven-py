# MIME parsing

mxRaven webhook payloads describe inbound mail as JSON, but the full message is
kept as raw RFC 5322 bytes behind the payload's `raw_email` reference.
`mxraven.mime` turns those bytes into headers, addresses, bodies, and decoded
attachments. It uses only the standard library.

## Parse bytes you already hold

```python
from mxraven.mime import parse_email

email = parse_email(raw_bytes)
print(email.subject, len(email.attachments))
for attachment in email.attachments:
    print(attachment.filename, attachment.content_type, len(attachment.content))
```

## Fetch and parse in one step

```python
from mxraven.mime import fetch_and_parse_raw_email
from mxraven.webhook import EventType, Verifier

verifier = Verifier(secret=signing_secret)
event = verifier.verify_and_decode(request)

if event.type is EventType.INBOUND_EMAIL and event.inbound_email is not None:
    email = fetch_and_parse_raw_email(event.inbound_email.raw_email)
    print(email.subject, email.text)
```

The download verifies the declared size and SHA-256 digest and sends the
payload's short-lived bearer token in the `Authorization` header. Do not log
that token.

The asyncio counterpart lives in `mxraven.aio.mime`:

```python
from mxraven.aio.mime import fetch_and_parse_raw_email

email = await fetch_and_parse_raw_email(event.inbound_email.raw_email)
```

## Parsed message

`ParsedEmail` carries:

| Field | Meaning |
| --- | --- |
| `headers` | Every header field, in order, with continuation lines unfolded. Values are not decoded. |
| `subject` | The decoded `Subject`. |
| `from_`, `to`, `cc`, `bcc`, `reply_to` | Parsed mailboxes with decoded display names. |
| `message_id` | The `Message-ID` without angle brackets. |
| `date` | The parsed `Date`, when present and parseable. |
| `text`, `html` | The first decoded `text/plain` and `text/html` bodies. |
| `attachments` | Every non-body part with decoded content, filename, content type, and `Content-ID`. |

`ParsedAttachment.inline` is true when a part is marked `inline` or carries a
`Content-ID`, which is how an inline image referenced by `cid:` from an HTML
body is identified.

## Parser behavior

The parser is defensive: malformed messages yield the best available
interpretation instead of an error. It understands `multipart/mixed`,
`multipart/alternative`, and `multipart/related`, decodes `base64` and
`quoted-printable` transfer encodings, decodes RFC 2047 encoded words, and
reassembles RFC 2231 parameters. Declared charsets are decoded; unknown charsets
fall back to UTF-8 and then Windows-1252. `message/rfc822` parts are surfaced as
attachments rather than parsed recursively.

Only structurally unbounded input is rejected, so an untrusted message cannot
exhaust the stack or memory:

| Error | Meaning |
| --- | --- |
| `MimeNestingError` | MIME nesting exceeds 20 levels. |
| `MimePartsError` | More than 2000 MIME entities. |
