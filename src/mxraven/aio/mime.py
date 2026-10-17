"""Asynchronous inbound email parsing for mxRaven deliveries.

The asyncio counterpart of :mod:`mxraven.mime`. :func:`parse_email` is pure and
shared with the synchronous surface; :func:`fetch_and_parse_raw_email`
downloads asynchronously.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mxraven.errors import MimeError, MimeNestingError, MimePartsError
from mxraven.mime.parse import (
    ParsedAddress,
    ParsedAttachment,
    ParsedEmail,
    ParsedHeader,
    parse_email,
)

if TYPE_CHECKING:
    from mxraven._internal.http import AsyncTransport
    from mxraven.webhook.payload import RawEmail

__all__ = [
    "MimeError",
    "MimeNestingError",
    "MimePartsError",
    "ParsedAddress",
    "ParsedAttachment",
    "ParsedEmail",
    "ParsedHeader",
    "fetch_and_parse_raw_email",
    "parse_email",
]

_DEFAULT_FETCH_TIMEOUT = 60.0


async def fetch_and_parse_raw_email(
    raw: RawEmail,
    *,
    timeout: float = _DEFAULT_FETCH_TIMEOUT,
    transport: AsyncTransport | None = None,
) -> ParsedEmail:
    """Download and parse an inbound-email payload's raw message.

    The asynchronous counterpart of
    :func:`mxraven.mime.fetch_and_parse_raw_email`; the download verifies the
    declared size and SHA-256 digest.

    Args:
        raw: The ``raw_email`` object from an inbound-email payload.
        timeout: The request timeout in seconds.
        transport: An optional asynchronous transport to use instead of the
            default.

    Returns:
        The parsed message. Attachment content is a fresh copy.

    Raises:
        WebhookError: The download failed or the size or digest did not match.
        MimeNestingError: The message nests deeper than the parser allows.
        MimePartsError: The message has more MIME entities than the parser
            allows.
    """
    return parse_email(await raw.fetch_async(timeout=timeout, transport=transport))
