"""Submission results.

These value objects are returned by :meth:`mxraven.Client.send` and
:meth:`mxraven.Client.send_raw`, and carried on
:class:`mxraven.SMTPTransactionError` when every recipient is rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mxraven.errors import MxRavenError

__all__ = ["RecipientResult", "Result", "parse_message_ref"]

_MESSAGE_REF_KEY = "message_ref="
_MESSAGE_REF_TERMINATORS = (">", ";", " ", "\t", "\r", "\n")


@dataclass(frozen=True, slots=True)
class RecipientResult:
    """Whether a single envelope recipient was accepted.

    Attributes:
        address: The recipient address as submitted, without angle brackets.
        accepted: Whether the server accepted the recipient.
        error: The rejection reason when `accepted` is false, otherwise `None`.
    """

    address: str
    accepted: bool
    error: MxRavenError | None = None


@dataclass(frozen=True, slots=True)
class Result:
    """The outcome of a submission.

    Attributes:
        message_ref: The mxRaven message reference parsed from the final
            ``DATA`` reply, or an empty string when the server did not report
            one. This is the durable reference to use for correlation.
        code: The final SMTP reply code.
        message: The final SMTP reply text. It is intended for humans and is
            not stable.
        recipients: Per-recipient acceptance, in envelope order.
    """

    message_ref: str = ""
    code: int = 0
    message: str = ""
    recipients: tuple[RecipientResult, ...] = ()


def parse_message_ref(message: str) -> str:
    """Extract the mxRaven message reference from a final ``DATA`` reply.

    The reference follows the literal token ``message_ref=``. A single leading
    ``<`` is removed, and the value ends at the first ``>``, ``;``, space, tab,
    carriage return, or line feed.

    Args:
        message: The final ``DATA`` reply text.

    Returns:
        The message reference, or an empty string when the reply does not carry
        one.
    """
    index = message.find(_MESSAGE_REF_KEY)
    if index < 0:
        return ""
    value = message[index + len(_MESSAGE_REF_KEY) :].strip()
    value = value.removeprefix("<")
    ends = [found for token in _MESSAGE_REF_TERMINATORS if (found := value.find(token)) >= 0]
    if ends:
        value = value[: min(ends)]
    return value
