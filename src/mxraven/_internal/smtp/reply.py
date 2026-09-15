"""SMTP reply parsing.

Pure helpers shared by the synchronous and asynchronous drivers. Nothing in
this module performs I/O; the drivers read lines and pass them here.

The wire rules mirror ``github.com/synqronlabs/raven/client``: a reply is one
or more lines, every line begins with the same three-digit code, and the reply
ends at the first line whose fourth byte is a space. Any other fourth byte
(including ``-``) continues the reply.
"""

from __future__ import annotations

from dataclasses import dataclass

from mxraven.errors import SMTPError, SMTPProtocolError

__all__ = ["Reply", "parse_enhanced_code", "parse_reply_line", "reply_from_lines"]

_MIN_LINE_LENGTH = 4
_ENHANCED_MIN_LENGTH = 5
_ENHANCED_PART_COUNT = 3
_CODE_DIGITS = frozenset("0123456789")
_SUCCESS_MIN = 200
_SUCCESS_MAX = 300
_INTERMEDIATE_MIN = 300
_INTERMEDIATE_MAX = 400
_TRANSIENT_MIN = 400
_TRANSIENT_MAX = 500
_PERMANENT_MIN = 500
_PERMANENT_MAX = 600


def parse_reply_line(raw: str) -> tuple[int, str, bool]:
    """Parse a single SMTP reply line.

    Args:
        raw: One reply line with its ``CRLF`` terminator removed.

    Returns:
        A tuple of the three-digit reply code, the message text (the line
        without its code and separator), and whether this line terminates the
        reply.

    Raises:
        SMTPProtocolError: The line is shorter than four characters or does not
            start with a three-digit code.
    """
    if len(raw) < _MIN_LINE_LENGTH:
        message = f"malformed reply line: {raw!r}"
        raise SMTPProtocolError(message)
    code_text = raw[:3]
    if not all(char in _CODE_DIGITS for char in code_text):
        message = f"invalid reply code: {raw!r}"
        raise SMTPProtocolError(message)
    return int(code_text), raw[4:], raw[3] == " "


def parse_enhanced_code(message: str) -> str:
    """Extract an RFC 3463 enhanced status code from the first reply line.

    The code is the first whitespace-delimited token, and it must have exactly
    three dot-separated all-digit components. Component signs are rejected, so
    ``2.-1.0`` is not an enhanced code.

    Args:
        message: The message text of the first reply line.

    Returns:
        The ``X.Y.Z`` code, or an empty string when none is present.
    """
    if len(message) < _ENHANCED_MIN_LENGTH:
        return ""
    token = message.split(" ", 1)[0]
    parts = token.split(".")
    if len(parts) != _ENHANCED_PART_COUNT:
        return ""
    for part in parts:
        if not part or not (part.isascii() and part.isdigit()):
            return ""
    return token


@dataclass(frozen=True, slots=True)
class Reply:
    """A complete SMTP reply.

    Attributes:
        code: The three-digit reply code.
        message: The reply's message text, with lines joined by newlines.
        lines: The per-line message text in wire order.
        enhanced_code: The RFC 3463 enhanced status code of the first line, or
            an empty string.
    """

    code: int
    message: str
    lines: tuple[str, ...]
    enhanced_code: str = ""

    @property
    def is_success(self) -> bool:
        """Whether the reply is a success (2xx)."""
        return _SUCCESS_MIN <= self.code < _SUCCESS_MAX

    @property
    def is_intermediate(self) -> bool:
        """Whether the reply is intermediate (3xx)."""
        return _INTERMEDIATE_MIN <= self.code < _INTERMEDIATE_MAX

    @property
    def is_transient_error(self) -> bool:
        """Whether the reply is a transient failure (4xx)."""
        return _TRANSIENT_MIN <= self.code < _TRANSIENT_MAX

    @property
    def is_permanent_error(self) -> bool:
        """Whether the reply is a permanent failure (5xx)."""
        return _PERMANENT_MIN <= self.code < _PERMANENT_MAX

    def to_error(self) -> SMTPError | None:
        """Convert the reply to an error when it is a failure.

        Returns:
            ``None`` for 2xx and 3xx replies, otherwise an :class:`SMTPError`
            carrying the code, enhanced code, and message.
        """
        if self.is_success or self.is_intermediate:
            return None
        return SMTPError(self.code, self.message, self.enhanced_code)


def reply_from_lines(lines: list[str]) -> Reply:
    """Build a :class:`Reply` from already-read wire lines.

    Args:
        lines: The reply lines, without terminators, in wire order.

    Returns:
        The assembled reply.

    Raises:
        SMTPProtocolError: No lines were supplied, a line is malformed, or the
            lines carry inconsistent reply codes.
    """
    messages: list[str] = []
    code: int | None = None
    for raw in lines:
        line_code, message, _final = parse_reply_line(raw)
        if code is None:
            code = line_code
        elif line_code != code:
            message = "inconsistent reply codes"
            raise SMTPProtocolError(message)
        messages.append(message)
    if code is None:
        message = "empty reply"
        raise SMTPProtocolError(message)
    joined = "\n".join(messages)
    return Reply(
        code=code,
        message=joined,
        lines=tuple(messages),
        enhanced_code=parse_enhanced_code(messages[0]),
    )
