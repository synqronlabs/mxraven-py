"""Download and parse the raw message referenced by an inbound-email payload."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mxraven.mime.parse import ParsedEmail, parse_email

if TYPE_CHECKING:
    from mxraven._internal.http import SyncTransport
    from mxraven.webhook.payload import RawEmail

__all__ = ["fetch_and_parse_raw_email"]

_DEFAULT_FETCH_TIMEOUT = 60.0


def fetch_and_parse_raw_email(
    raw: RawEmail,
    *,
    timeout: float = _DEFAULT_FETCH_TIMEOUT,
    transport: SyncTransport | None = None,
) -> ParsedEmail:
    """Download the raw message an inbound-email payload references and parse it.

    The download verifies the declared size and SHA-256 digest and is bounded
    exactly like :meth:`mxraven.webhook.RawEmail.fetch`; the payload's
    short-lived bearer token is sent in the ``Authorization`` header and must
    not be logged.

    Args:
        raw: The ``raw_email`` object from an inbound-email payload.
        timeout: The request timeout in seconds.
        transport: An optional transport to use instead of the default.

    Returns:
        The parsed message. Attachment content is a fresh copy.

    Raises:
        WebhookError: The download failed or the size or digest did not match.
        MimeNestingError: The message nests deeper than the parser allows.
        MimePartsError: The message has more MIME entities than the parser
            allows.
    """
    return parse_email(raw.fetch(timeout=timeout, transport=transport))
