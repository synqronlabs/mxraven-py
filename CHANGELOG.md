# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Entries are assembled from the fragments in `changelog.d/` by
[towncrier](https://towncrier.readthedocs.io).

<!-- towncrier release notes start -->

## 0.4.0 - 2026-10-21

### Features

- Implement the initial mail-facing SDK: SMTP submission over a bespoke
  socket/TLS client with a bounded connection pool (sync and asyncio), message
  composition, full envelope parameter support (SIZE, SMTPUTF8, 8BITMIME, DSN,
  REQUIRETLS, DELIVERBY, BDAT/CHUNKING), webhook signature verification and
  payload decoding, and recipient feedback with RFC 8058 one-click unsubscribe. ([#1](https://github.com/synqronlabs/mxraven-py/issues/1))
- Expose the RFC 8689 ``REQUIRETLS`` and RFC 2852 ``DELIVERBY`` submission
  extensions on the public API: ``Message.require_tls()`` and
  ``Message.deliver_by()`` for composed messages, and ``require_tls`` /
  ``delivery_by`` fields on ``Envelope`` for raw messages, with the new
  ``DeliveryBy`` and ``DeliveryByMode`` value types. ([#2](https://github.com/synqronlabs/mxraven-py/issues/2))
- Encode text and HTML bodies as quoted-printable when any line would exceed the
  RFC 5322 limit of 998 octets, so serialized messages always satisfy the
  line-length rule. The encoder wraps at 76 columns with soft line breaks and
  preserves trailing whitespace; ASCII and non-ASCII bodies without over-long
  lines keep their existing ``7bit`` / ``8bit`` encodings. ([#3](https://github.com/synqronlabs/mxraven-py/issues/3))
- Add client notification consumption over NATS JetStream as ``mxraven.notify``
  and its asyncio counterpart ``mxraven.aio.notify``. A listener's accepted
  messages and delivery attempts can now be consumed with the same API key as
  SMTP submission, with per-event acknowledgement, backoff redelivery, and
  decoding into the webhook ``InboundEmail`` payload and the new ``AttemptEvent``.
  The transport ships as the optional ``notify`` extra so the core SDK stays
  dependency-free. Also sync the webhook payload shape with the notification
  producers: add the ``invalid`` ``StatusOutcome``, the ``unspecified``
  ``TerminalAction``, and the ``reason`` field on ``DeliveryStatus``. ([#4](https://github.com/synqronlabs/mxraven-py/issues/4))
- Add inbound MIME parsing as ``mxraven.mime`` and its asyncio counterpart
  ``mxraven.aio.mime``. ``parse_email`` turns raw RFC 5322 bytes into headers,
  addresses, text and HTML bodies, and decoded attachments; ``fetch_and_parse_raw_email``
  downloads the raw message an inbound-email webhook payload references, verifies
  its size and SHA-256 digest, and parses it in one step. The parser is defensive
  (it understands ``multipart/mixed``, ``multipart/alternative``, and
  ``multipart/related``, decodes ``base64``/``quoted-printable`` and RFC 2047/2231
  parameters, and falls back across charsets) and rejects only structurally
  unbounded input (nesting depth and part count). ([#5](https://github.com/synqronlabs/mxraven-py/issues/5))
