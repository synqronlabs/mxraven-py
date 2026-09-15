# mxRaven Python SDK

A Python SDK for the mxRaven **mail-facing** runtime surfaces:

- **SMTP submission** — the root package (`mxraven`).
- **Webhook verification and decoding** — `mxraven.webhook`.
- **Recipient feedback and RFC 8058 one-click unsubscribe** — `mxraven.feedback`.

It is the Python counterpart of the Go SDK at
[`github.com/synqronlabs/mxraven-go/mail`](https://github.com/synqronlabs/mxraven-go/tree/main/mail)
and the TypeScript SDK
[`@mxraven/mail`](https://github.com/synqronlabs/mxraven-js), and exposes the
same capabilities behind an idiomatic Python API.

> **Status:** early development. The public API is not yet stable.

Control-plane administration — tenants, domains, suppression lists,
deliverability reporting, mail analytics — is **out of scope**. It lives in the
separate control-plane SDK.

## Requirements

Python 3.11 or later. The SDK has **zero runtime dependencies**; it uses only
the standard library.

## Install

```sh
uv add mxraven
# or
pip install mxraven
```

## Quick start

```python
import os

from mxraven import Client, Message

client = Client(
    host="smtp.mxraven.com",
    username="mxr_tx_ab12cd34ef56",
    secret=os.environ["MXRAVEN_SECRET"],
)

try:
    result = client.send(
        Message()
        .from_("Acme <noreply@acme.example>")
        .to("customer@example.com")
        .subject("Your receipt")
        .text("Thanks for your order.")
        .html("<p>Thanks for your order.</p>")
    )
    print("accepted as", result.message_ref)
finally:
    client.close()
```

The submission service requires STARTTLS and SMTP AUTH, so both are always
used. The SDK does not read environment variables itself. Load secrets from a
secret manager in production.

### Sending raw messages

Use `send_raw` to stream an already serialized RFC 5322 message with an explicit
envelope. The caller is responsible for RFC 5322 correctness.

```python
client.send_raw(
    {"from": "bounce@acme.example", "to": ["customer@example.com"]},
    raw_message_bytes,
)
```

### Errors

Rejected commands raise `SMTPError`; when every recipient is rejected,
`SMTPTransactionError` is raised with the per-recipient `result`.

```python
from mxraven import SMTPError

try:
    client.send(message)
except SMTPError as error:
    if error.permanent:
        ...  # 5xx: do not retry the same message.
```

## Webhooks

Verification and decoding use the webhook signing secret, not the submission API
key.

```python
from mxraven.webhook import Verifier, event_type

verifier = Verifier(secret=os.environ["MXRAVEN_WEBHOOK_SECRET"])
event = verifier.verify_and_decode(request)  # any adapter or raw primitives

if event.type is event_type.inbound_email:
    raw = event.inbound_email.raw_email.fetch()
```

The signature is HMAC-SHA256 over a canonical request string and is sent in the
`X-MxRaven-*` headers. The signing secret is used as literal key bytes; do not
base64-decode it.

## Feedback

Teach the spam filter with the exact raw message bytes mxRaven processed, or
perform a one-click unsubscribe. Learning uses the same submission API key as
SMTP submission, over HTTP Basic auth.

```python
from mxraven.feedback import Client as FeedbackClient

feedback = FeedbackClient(
    base_url="https://feedback.mxraven.com",
    username="mxr_tx_ab12cd34ef56",
    secret=os.environ["MXRAVEN_SECRET"],
)
feedback.learn_spam(raw_message_bytes)
```

## Development

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```sh
make install   # uv sync: create .venv and install dev dependencies
make format    # ruff format + safe fixes
make lint      # ruff format --check + ruff check
make typecheck # mypy
make test      # pytest
make check     # every quality gate, including build
make docs      # mkdocs build --strict
```

`make check` mirrors the CI job and must pass before a change is considered
complete.

## Toolchain

| Concern                | Tool                                                                     |
| ---------------------- | ------------------------------------------------------------------------ |
| Environment / deps     | [uv](https://docs.astral.sh/uv/)                                         |
| Build backend          | [hatchling](https://hatch.pypa.io) + [hatch-vcs](https://github.com/ofek/hatch-vcs) |
| Lint / format          | [Ruff](https://docs.astral.sh/ruff/) (`ruff check` + `ruff format`)      |
| Type checking          | [mypy](https://mypy.readthedocs.io) (`strict`)                           |
| Testing                | [pytest](https://docs.pytest.org) + [pytest-cov](https://pytest-cov.readthedocs.io), [trustme](https://trustme.readthedocs.io) |
| API docs               | [MkDocs Material](https://squidfunk.github.io/mkdocs-material/) + [mkdocstrings](https://mkdocstrings.github.io) |
| Versioning / changelog | [hatch-vcs](https://github.com/ofek/hatch-vcs) + [towncrier](https://towncrier.readthedocs.io) |
| Release                | GitHub Actions with PyPI Trusted Publishing and attestations             |

## Releases

Releases are cut from `vMAJOR.MINOR.PATCH` git tags. The version is derived from
the tag by hatch-vcs, and `CHANGELOG.md` is assembled from the fragments in
`changelog.d/`. Publishing runs from GitHub Actions via PyPI Trusted Publishing
(OIDC) with build attestations; do not publish manually.

## License

Apache License 2.0. See [LICENSE](./LICENSE) and [NOTICE](./NOTICE).
