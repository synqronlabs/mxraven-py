"""mxRaven webhook signature verification."""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from mxraven.errors import InvalidSignatureError, WebhookError
from mxraven.webhook.decode import decode

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from mxraven.webhook.payload import Event
    from mxraven.webhook.request import WebhookRequest

__all__ = ["Verifier"]

HEADER_WEBHOOK_ID = "x-mxraven-webhook-id"
HEADER_TIMESTAMP = "x-mxraven-timestamp"
HEADER_SIGNATURE = "x-mxraven-signature"
HEADER_SIGNATURE_KID = "x-mxraven-signature-kid"

_DEFAULT_TOLERANCE = 300.0
_DEFAULT_MAX_BODY = 1 << 20
_SIGNATURE_SCHEME = "sha256="
_DIGITS = frozenset("0123456789")


class Verifier:
    """Verifies the signature of an mxRaven webhook request.

    A verifier is safe for concurrent use once constructed. Configure it with a
    signing secret, with per-key secrets for rotation, or both.

    Example:
        ```python
        verifier = Verifier(secret=signing_secret)
        event = verifier.verify_and_decode(request)
        ```

    Args:
        secret: The signing secret, used as literal bytes and never decoded.
        keys: Per-key secrets for rotation, keyed by signing key ID.
        tolerance: The maximum accepted clock skew in seconds; ``0`` disables
            the timestamp check. Defaults to five minutes.
        max_body_bytes: The maximum body size in bytes. Defaults to 1 MiB.
        clock: A callable returning the current Unix time; used for testing.

    Raises:
        ValueError: No secret was supplied or an option is invalid.
    """

    def __init__(
        self,
        *,
        secret: str = "",
        keys: Mapping[str, str] | None = None,
        tolerance: float = _DEFAULT_TOLERANCE,
        max_body_bytes: int = _DEFAULT_MAX_BODY,
        clock: Callable[[], float] | None = None,
    ) -> None:
        """Initialize the verifier."""
        self._secret = secret
        self._keys: dict[str, str] = {}
        for kid, value in (keys or {}).items():
            if not kid.strip():
                message = "webhook: signing key ID must not be empty"
                raise ValueError(message)
            if value == "":
                message = "webhook: signing secret must not be empty"
                raise ValueError(message)
            self._keys[kid] = value
        if secret == "" and not self._keys:
            message = "webhook: a signing secret is required"
            raise ValueError(message)
        if tolerance < 0:
            message = "webhook: tolerance must not be negative"
            raise ValueError(message)
        if max_body_bytes <= 0:
            message = "webhook: maximum body size must be positive"
            raise ValueError(message)
        self._tolerance = tolerance
        self._max_body_bytes = max_body_bytes
        self._clock = clock if clock is not None else time.time

    def verify(self, request: WebhookRequest) -> None:
        """Verify a request's signature.

        Args:
            request: The inbound request.

        Raises:
            InvalidSignatureError: The signature does not match.
            WebhookError: A required header is missing, the timestamp is stale,
                or the body exceeds the configured limit.
        """
        self._check(request)

    def verify_and_decode(self, request: WebhookRequest) -> Event:
        """Verify a request and decode its payload.

        Args:
            request: The inbound request.

        Returns:
            The decoded event.

        Raises:
            InvalidSignatureError: The signature does not match.
            WebhookError: Verification or decoding failed.
        """
        self._check(request)
        return decode(request.body)

    def _check(self, request: WebhookRequest) -> None:
        if len(request.body) > self._max_body_bytes:
            message = f"webhook: request body exceeds {self._max_body_bytes} bytes"
            raise WebhookError(message)
        webhook_id = request.header(HEADER_WEBHOOK_ID).strip()
        if webhook_id == "":
            message = "webhook: missing webhook ID header"
            raise WebhookError(message)
        timestamp = request.header(HEADER_TIMESTAMP).strip()
        if timestamp == "":
            message = "webhook: missing timestamp header"
            raise WebhookError(message)
        signature = request.header(HEADER_SIGNATURE).strip()
        if signature == "":
            message = "webhook: missing signature header"
            raise WebhookError(message)

        self._check_timestamp(timestamp)
        secret = self._secret_for(request.header(HEADER_SIGNATURE_KID))

        if not signature.startswith(_SIGNATURE_SCHEME):
            message = "webhook: unsupported signature algorithm"
            raise WebhookError(message)
        try:
            provided = bytes.fromhex(signature[len(_SIGNATURE_SCHEME) :])
        except ValueError as error:
            message = "webhook: malformed signature"
            raise WebhookError(message) from error

        expected = _sign(
            secret=secret,
            method=request.method,
            url=request.url,
            timestamp=timestamp,
            webhook_id=webhook_id,
            body=request.body,
        )
        if not hmac.compare_digest(provided, expected):
            raise InvalidSignatureError

    def _check_timestamp(self, timestamp: str) -> None:
        if self._tolerance <= 0:
            return
        if not timestamp or not all(char in _DIGITS for char in timestamp):
            message = f"webhook: invalid timestamp {timestamp!r}"
            raise WebhookError(message)
        skew = abs(self._clock() - int(timestamp))
        if skew > self._tolerance:
            message = "webhook: timestamp is outside the accepted clock skew"
            raise WebhookError(message)

    def _secret_for(self, kid: str) -> str:
        value = kid.strip()
        if self._keys:
            if value == "":
                message = "webhook: missing signature key ID"
                raise WebhookError(message)
            secret = self._keys.get(value)
            if secret is None:
                message = f"webhook: unknown signature key ID {value!r}"
                raise WebhookError(message)
            return secret
        return self._secret


def _sign(
    *,
    secret: str,
    method: str,
    url: str,
    timestamp: str,
    webhook_id: str,
    body: bytes,
) -> bytes:
    parts = urlsplit(url)
    target = parts.path if parts.path else "/"
    if parts.query:
        target = f"{target}?{parts.query}"
    canonical = "\n".join(
        [
            timestamp,
            webhook_id,
            method,
            parts.netloc.lower(),
            target,
            hashlib.sha256(body).hexdigest(),
        ]
    )
    return hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).digest()
