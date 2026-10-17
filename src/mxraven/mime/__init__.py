"""Inbound email parsing for mxRaven deliveries.

mxRaven webhook payloads describe inbound mail as JSON, and the full message
remains available as raw RFC 5322 bytes behind the payload's ``raw_email``
reference. :func:`parse_email` turns those bytes into headers, addresses,
bodies, and decoded attachments; :func:`fetch_and_parse_raw_email` downloads,
verifies, and parses them in one step.

Example:
    ```python
    from mxraven.mime import fetch_and_parse_raw_email
    from mxraven.webhook import Verifier, EventType

    verifier = Verifier(secret=signing_secret)
    event = verifier.verify_and_decode(request)

    if event.type is EventType.INBOUND_EMAIL and event.inbound_email is not None:
        email = fetch_and_parse_raw_email(event.inbound_email.raw_email)
        print(email.subject, len(email.attachments))
    ```

The parser is defensive: malformed messages yield the best available
interpretation instead of an error, and unknown charsets fall back to UTF-8 and
then Windows-1252. It understands ``multipart/mixed``,
``multipart/alternative``, and ``multipart/related``, decodes ``base64`` and
``quoted-printable`` transfer encodings, decodes RFC 2047 encoded words, and
reassembles RFC 2231 parameters. ``message/rfc822`` parts are surfaced as
attachments rather than parsed recursively. Only unbounded input -- very deep
nesting or thousands of parts -- is rejected.
"""

from __future__ import annotations

from mxraven.errors import MimeError, MimeNestingError, MimePartsError
from mxraven.mime.fetch import fetch_and_parse_raw_email
from mxraven.mime.parse import (
    ParsedAddress,
    ParsedAttachment,
    ParsedEmail,
    ParsedHeader,
    parse_email,
)

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
