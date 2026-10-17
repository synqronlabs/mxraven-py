# AGENTS.md

## Project

`mxraven-py` is the Python SDK for the **mail-facing** mxRaven runtime
surfaces:

- **SMTP submission** — the root package (`mxraven`).
- **Webhook verification and decoding** — `mxraven.webhook`.
- **Inbound MIME parsing** — `mxraven.mime` and `mxraven.aio.mime`.
- **Recipient feedback and RFC 8058 one-click unsubscribe** — `mxraven.feedback`.
- **Client notification events over NATS JetStream** — `mxraven.notify` and
  `mxraven.aio.notify`. The transport is the optional `notify` extra; the rest
  of the SDK stays dependency-free.

It is the Python counterpart of:

- `github.com/synqronlabs/mxraven-go/mail` (Go SDK, with `admin`),
- `@mxraven/mail` (TypeScript SDK), and
- `github.com/synqronlabs/raven` (the Go SMTP library and message builder that
  the Go SDK wraps).

All three are checked out under `tmp/` (gitignored) for reference. Read them to
understand behaviour, wire formats, and edge cases, but **never import from,
depend on, or ship anything under `tmp/`**. `tmp/` is not part of the package or
the test suite.

Control-plane administration — tenants, domains, suppression lists,
deliverability reporting, mail analytics, API-key management — is **out of
scope**. It lives in a separate control-plane SDK.

## Core Principles

- Write code that is easy to read, review, test, and maintain.
- Prefer the Python standard library. The SDK has **zero runtime dependencies**
  and should keep it that way unless a dependency provides clear, substantial
  value that cannot be reasonably implemented locally.
- Keep abstractions proportional to the problem. Do not add protocols, generics,
  wrappers, or configuration layers speculatively.
- Minimize the exported API. Every public name is a long-lived compatibility
  commitment.
- Preserve backward compatibility unless a breaking change is intentional,
  documented, and released under the appropriate SemVer major version.
- Leave the codebase simpler than you found it.

## Tech Stack

| Concern                | Tool                                                      | Config                     |
| ---------------------- | --------------------------------------------------------- | -------------------------- |
| Environment / deps     | uv (lockfile committed)                                   | `pyproject.toml`, `uv.lock` |
| Build backend          | hatchling + hatch-vcs (version from git tags)             | `pyproject.toml`           |
| Lint / format          | Ruff (`ruff check` + `ruff format`)                       | `[tool.ruff]`              |
| Type checking          | mypy (`strict`)                                           | `[tool.mypy]`              |
| Testing                | pytest + pytest-cov; trustme for TLS test certificates    | `[tool.pytest.ini_options]` |
| API docs               | MkDocs Material + mkdocstrings (Google-style docstrings)  | `mkdocs.yml`               |
| Versioning / changelog | hatch-vcs + towncrier (`changelog.d/`)                    | `[tool.towncrier]`         |
| Release                | GitHub Actions, PyPI Trusted Publishing with attestations | `.github/workflows/`       |

## Commands

Always run tooling through `uv run`; do not activate the virtual environment by
hand.

```sh
make install          # uv sync
make format           # ruff format + ruff check --fix
make lint             # ruff format --check + ruff check
make typecheck        # mypy
make test             # pytest
make coverage         # pytest --cov
make check            # format check + lint + typecheck + test + build
make build            # uv build
make docs             # mkdocs build --strict
make pre-commit       # install git hooks
```

`make check` mirrors CI and must pass before a change is considered complete. A
single Python version is enough locally; CI covers 3.11–3.14.

## Python Style

### Packaging and modules

- Source lives under `src/mxraven/` (src layout). Never add importable code at
  the repository root.
- The package ships `src/mxraven/py.typed`. Keep it; downstream type checkers
  rely on it.
- Start every module with `from __future__ import annotations`. Annotations are
  never evaluated at runtime.
- Use absolute imports inside the package (`from mxraven._internal.smtp import
  pool`). Relative imports are acceptable within a subpackage but keep them
  short.
- Put implementation details in `mxraven._internal` (and `_`-prefixed modules).
  Nothing there is public; it may change without notice or a version bump.
- Public subpackages define their surface in `__init__.py` and set `__all__`.

### Types

- mypy runs in `strict` mode. Do not weaken it, and do not add per-line
  `# type: ignore` without a specific error code and an adjacent justification.
- **Do not use `Any`.** Prefer precise types, `object`, or a `TypeVar` bound.
  `Any` is acceptable only at an unavoidable third-party boundary, documented in
  place.
- Prefer `collections.abc` protocols (`Iterable`, `Sequence`, `Mapping`,
  `Callable`) for inputs, and concrete types for outputs.
- Accept `bytes` / `bytearray` / `memoryview` for raw data through a
  `Buffer`-like union or `bytes | bytearray | memoryview`; return `bytes`.
- Use `@dataclass(frozen=True, slots=True)` for value types (results, decoded
  payloads, options).
- Use `enum.StrEnum` for closed wire values (`Disposition`, `EventType`,
  `StatusOutcome`, `TerminalAction`). Adding members later is allowed; document
  it.
- Use lowercase built-in generics (`list[str]`, `dict[str, str]`) and `X | None`
  rather than `Optional`/`Union`.
- Public functions and methods are always fully annotated, including `-> None`.

### Naming

- Follow PEP 8. `snake_case` for functions, methods, variables, parameters, and
  modules; `PascalCase` for classes and type aliases; `UPPER_SNAKE_CASE` only
  for true module-level constants.
- Prefix booleans with `is_`, `has_`, `can_`, or `should_`.
- Do not use Hungarian prefixes (`IUser`, `TValue`).
- Match the Go/JS SDK's public concept names where practical, converted to
  `snake_case`, so the SDKs stay recognizable: `Client`, `Message`, `Envelope`,
  `Result`, `RecipientResult`, `SMTPError`, `Verifier`, `Event`,
  `InboundEmail`, `DeliveryStatus`, `RawEmail`, `Disposition`.
- Avoid `from_`-style workarounds except where a name collides with a Python
  keyword (`Message.from_`, since `from` is reserved). Document the reason.

### Functions and control flow

- Keep functions small and focused on one responsibility. Prefer early returns
  over nested conditionals.
- Prefer keyword-only arguments (`*`) for anything with more than one
  same-typed parameter, or where positional order is easy to confuse.
- Prefer a single options object or keyword-only parameters over several
  positional parameters.
- Use `with` for every resource that supports it. Never rely on `__del__` for
  cleanup; provide `close()` and implement the context-manager protocol on
  clients.
- Do not use bare `except:`; catch the narrowest exception and re-raise with
  context (`raise ... from err`).
- Prefer `pathlib.Path` over `os.path` when paths are involved.
- Where the Go SDK takes a `context.Context`, Python uses an explicit
  `timeout: float | None` and cooperative cancellation. Document the semantics.

## Public API Design

Treat every exported name as long-lived API.

- Keep the public surface as small as possible and set `__all__` explicitly.
- Do not export implementation details or `_internal` helpers.
- New public APIs require Google-style docstrings and tests.
- Prefer APIs that are hard to misuse.
- Do not expose a protocol or abstract base class solely so callers can mock an
  implementation. Accept a concrete object or a tiny callable where injection is
  genuinely needed.
- Avoid parameter lists containing several values of the same type; use
  keyword-only arguments or a frozen options dataclass.
- Document ownership, mutation, and threading rules for anything returned or
  accepted by reference (buffers, mutable objects, connections).
- Prefer returning immutable value objects over tuples for anything with more
  than two fields.

## SMTP Implementation

Submission is implemented **directly on `socket` + `ssl`**, mirroring
`tmp/raven/client`. Do **not** reach for `smtplib`: the service requires precise
control over greeting handling, mandatory STARTTLS ordering, pipelined envelope
commands, reply parsing, per-recipient results, connection reuse, and error
translation. Use `smtplib` only as a reference for corner cases, never as the
transport.

Behaviour to preserve:

- Connect, read the multi-line greeting, and send `EHLO <local_name>`,
  falling back to `HELO` when `EHLO` is rejected.
- **STARTTLS is mandatory and ordered before AUTH.** Send `STARTTLS`, require a
  2xx, wrap the socket, then re-issue `EHLO` before authenticating. Never
  proceed in plaintext.
- AUTH `PLAIN` and `LOGIN`, selecting the mechanism the server advertises, with
  a preference order. Treat authentication failure as a hard error.
- `MAIL FROM:<...>` (supporting the null reverse-path `<>`), one `RCPT TO` per
  recipient, then `DATA`. When the server advertises `PIPELINING`, batch
  envelope commands and read exactly one reply per command; otherwise run
  sequentially.
- The message body is terminated with `CRLF.CRLF`. Perform **dot-stuffing** on
  any line beginning with `.`, normalize line endings to `CRLF`, and ensure the
  body ends with `CRLF`.
- Track per-recipient acceptance. Continue after some recipients are rejected,
  but abort the transaction when **all** recipients are rejected and send a
  best-effort `RSET`.
- Parse the three-digit reply code and the RFC 3463 `X.Y.Z` enhanced status code
  from the first reply line. Expose both on `SMTPError`.
- Parse the muxRaven `message_ref=<uuid>` token from the final `DATA` reply.
- Keep a bounded, thread-safe pool of authenticated connections (default 5). A
  connection whose command fails is **discarded**, never returned to the pool,
  because a failed command can leave unread replies on the wire.
- Provide explicit connect, read, and write timeouts, defaulting to 30 s, 5 min,
  and 5 min respectively, matching the reference client.

Defaults and safety:

- TLS uses `ssl.create_default_context()`, requires a valid certificate chain,
  sets `server_hostname` from the configured host, and enforces TLS 1.2+.
  Verification is never disabled. A caller may inject an `ssl.SSLContext`.
- Never log credentials, the SMTP secret, or the webhook signing secret.
- Bound every read (greeting, replies, body) and keep a maximum response line
  length.

## Error Handling

**Do not just check errors; handle them gracefully.** Exceptions are part of the
public API.

- Define a small hierarchy rooted at `MxRavenError`. Raise it for runtime and
  service failures.
- Use `SMTPError` (`code`, `enhanced_code`, `message`, `.permanent`,
  `.transient`) for rejected SMTP commands, `SMTPTransactionError` (carrying
  `.result`) when every recipient is rejected, `InvalidSignatureError` for a
  failed webhook signature, and `FeedbackError` (`status_code`, `.retryable`)
  for non-success feedback responses.
- Raise `ValueError` or `TypeError` for programmer errors such as invalid
  constructor arguments; reserve custom exceptions for conditions callers may
  need to handle.
- Preserve the original cause with `raise ... from err`, and add context only
  when the layer contributes useful information. Do not wrap mechanically at
  every frame.
- Never make control flow depend on matching an exception message string. Branch
  on exception type and documented attributes.
- Document raised exceptions in the docstring with a `Raises:` section when they
  are part of the contract.
- Do not log ordinary operation errors in library code. Return or raise them and
  let the application decide how to report them.
- Do not swallow errors. If `close()` can fail and the failure matters,
  propagate it.

## Docstrings

Documentation is part of the API. Ruff enforces pydocstyle (`D`) with the
**Google** convention, and mkdocstrings renders the reference from docstrings.

- Every public module, class, function, method, and constant has a docstring.
- Start with a one-line summary in the imperative mood ending in a period.
- Use `Args:`, `Returns:`, `Raises:`, `Yields:`, and `Attributes:` sections as
  appropriate. Use `Note:` / `Warning:` admonitions for invariants, ownership,
  and concurrency rules.
- Explain **why**, defaults, invariants, and error semantics. Do not restate the
  signature.
- Reference identifiers with backticks, not reST roles.
- Add a short fenced `python` example whenever correct use is not obvious.

Example:

```python
def send(self, message: Message, *, timeout: float | None = None) -> Result:
    """Submit a composed message and return the server's result.

    Args:
        message: The message to submit. It may be sent more than once.
        timeout: An overall deadline in seconds, or `None` to use the client
            defaults.

    Returns:
        The server's result for the submission, including the mxRaven
        `message_ref` and per-recipient acceptance.

    Raises:
        SMTPError: The server rejected a command.
        SMTPTransactionError: Every recipient was rejected; the per-recipient
            detail is on the exception's `result`.
    """
```

## Testing

- Tests live under `tests/`, mirroring the package layout, and use pytest.
- Test observable behaviour and the public contract, not implementation details.
- Cover edge cases and failure paths. Assert exception semantics with
  `pytest.raises(...)` and attribute checks.
- Prefer `pytest.mark.parametrize` for table-driven cases over repetitive tests.
- Keep tests independent and order-insensitive. Use fixtures for setup and
  teardown.
- **Never depend on the real network.** The SMTP client is tested against a
  local in-process fake server bound to `127.0.0.1` on an ephemeral port. Use
  `trustme` to mint test certificate authorities and leaf certificates for
  STARTTLS tests instead of committing PEM files. Stub HTTP with an injected
  transport or a local `http.server`.
- Use `tmp_path`/`tmp_path_factory` for temporary files and `monkeypatch` for
  environment and clock control; never use real sleeps for synchronization.
- Do not assert on complete error message strings unless the human-readable
  output is explicitly contractual.
- Add a regression test for every bug fix.
- **Port the relevant cases from the Go SDK and `raven` test suites** whenever
  behaviour mirrors them (`tmp/mxraven-go`, `tmp/raven`). Parity must be
  verified, not assumed, especially for:
  - webhook signature canonicalization (share byte-exact test vectors),
  - `message_ref` parsing,
  - enhanced status code parsing and error translation,
  - per-recipient acceptance and the all-rejected path,
  - raw-email size and SHA-256 verification,
  - feedback error mapping.
- `pytest` runs with `filterwarnings = error`. Do not add global warning
  suppressions; fix the source or use a narrowly scoped `pytest.warns`.

## Concurrency

- Document whether each public type is safe for concurrent use.
- The SMTP client maintains a bounded pool of connections. Keep the pool free of
  data races and do not hold a lock across blocking I/O that can call back into
  user code.
- Prefer synchronous, deterministic logic unless concurrency provides a
  measurable or structural benefit. An asynchronous surface, if added later,
  should live behind a separate module and share the protocol implementation.
- Always give threads and sockets a clear termination path. Provide `close()`
  and the context-manager protocol.

## Security

- Never log or embed secrets: the submission secret, the webhook signing secret,
  and raw-email access tokens are all sensitive.
- Compare signatures and tokens with a timing-safe comparison
  (`hmac.compare_digest`), never `==`.
- Treat all external input (SMTP replies, HTTP responses, webhook bodies) as
  untrusted and bound it before use (size limits, maximum line lengths, strict
  JSON decoding).
- Keep TLS verification enabled by default with a sane minimum version. Never
  ship `ssl._create_unverified_context` or `CERT_NONE`.
- Set explicit timeouts on every socket and HTTP request.
- Do not use `pickle`, `eval`, `exec`, or `subprocess` with external input.

## Dependencies

Before adding a dependency:

1. determine whether the standard library is sufficient;
2. evaluate the maintenance, bundle, and compatibility cost;
3. confirm it solves substantially more than a small local implementation
   would;
4. avoid exposing dependency-specific types through the public API.

The runtime dependency list is empty by design. Tooling dependencies are allowed
in the `dev` dependency group. A new runtime dependency requires a strong
justification and a discussion in the change description.

## Build and Packaging

- `uv build` produces the sdist and wheel. **Never edit build outputs;**
  `dist/` is gitignored.
- The version is derived from git tags by hatch-vcs; `0.1.0` is the fallback in
  a tree with no tags. Do not hard-code a version.
- Public entry points are the root package and the `mxraven.webhook` /
  `mxraven.feedback` / `mxraven.notify` (with `mxraven.aio.notify`) / `mxraven.mime`
  (with `mxraven.aio.mime`) subpackages. Adding a subpackage requires updating
  `mkdocs.yml` and the docs nav.
- Run `uv run check-wheel-contents dist/*.whl` and `uv run twine check dist/*`
  whenever packaging changes. Keep `py.typed` and `license-files` correct.
- Keep `uv.lock` committed and in sync (`uv lock`, then `uv sync`).

## Versioning and Releases

- The project follows Semantic Versioning. `feat` → minor, `fix` → patch,
  breaking → major.
- Record every user-visible change with a towncrier fragment in `changelog.d/`
  named `<issue>.<type>.md` (for example `42.feature.md`). Types are `feature`,
  `fix`, `docs`, and `misc`.
- Deprecate before removing public APIs whenever practical. Emit
  `warnings.warn(..., DeprecationWarning, stacklevel=2)`, document the
  replacement, and remove only in a major release.
- Releases run through the GitHub Actions release workflow, which publishes to
  PyPI with Trusted Publishing and attestations. Do not publish manually from a
  workstation.

## Commits

Use Conventional Commits:

```text
<type>[optional scope][!]: <description>
```

Common types: `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `build`, `ci`,
`chore`. Use imperative, concise descriptions without a trailing period. Mark
breaking changes with `!` and a `BREAKING CHANGE:` footer. Keep commits focused;
do not mix unrelated refactoring with functional changes. Only commit when
explicitly asked.

## Definition of Done

A change is complete when:

- the implementation is idiomatic, appropriately simple Python;
- error cases are handled deliberately, preserve the cause, and use the
  documented exception hierarchy;
- exported APIs have complete Google-style docstrings and a `Raises:` section
  where errors are part of the contract;
- tests cover the new or changed behaviour, including failure paths, without
  touching the real network;
- `make check` succeeds (format, lint, typecheck, test, build);
- `make docs` succeeds (`mkdocs build --strict`);
- public compatibility and the required SemVer impact have been considered;
- a towncrier fragment describes the user-visible effect;
- anything mirrored from the Go/JS/raven repositories has a parity test or a
  documented, deliberate divergence.
