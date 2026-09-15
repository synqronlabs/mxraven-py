"""mxRaven feedback client.

Submits tenant spam/ham training and performs RFC 8058 one-click unsubscribes.
Learning uses the same submission API key as SMTP submission, over HTTP Basic
auth.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING
from urllib.parse import quote

from mxraven._internal.http import DEFAULT_ASYNC_TRANSPORT, DEFAULT_SYNC_TRANSPORT
from mxraven.errors import FeedbackError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from mxraven._internal.http import AsyncTransport, HttpResponse, SyncTransport

__all__ = ["AsyncClient", "Client", "Disposition", "LearningResult"]

_DEFAULT_TIMEOUT = 30.0
_HTTP_OK = 200
_HTTP_SUCCESS_MAX = 300
_MAX_ERROR_BODY = 4096
_LEARN_CONTENT_TYPE = "message/rfc822"
_UNSUBSCRIBE_CONTENT_TYPE = "application/x-www-form-urlencoded"
_UNSUBSCRIBE_BODY = b"List-Unsubscribe=One-Click"


class Disposition(StrEnum):
    """The training label for a message."""

    SPAM = "spam"
    HAM = "ham"


@dataclass(frozen=True, slots=True)
class LearningResult:
    """The outcome of a successful learning request.

    Attributes:
        status: The service status, normally ``learned``.
        disposition: The training label that was applied.
        tenant_id: The tenant that owns the matched message.
        listener_id: The listener that processed the matched message.
        matched_hash_kind: Which stored hash matched the submitted bytes.
    """

    status: str = ""
    disposition: str = ""
    tenant_id: str = ""
    listener_id: str = ""
    matched_hash_kind: str = ""


class Client:
    """Calls the mxRaven feedback service synchronously.

    A client is safe for concurrent use.

    Args:
        base_url: The feedback service base URL, for example
            ``https://feedback.mxraven.com``.
        username: The submission API key username, used for learning.
        secret: The submission API key secret, used for learning.
        timeout: The request timeout in seconds. Defaults to ``30``.
        transport: An optional HTTP transport to use instead of the default.

    Raises:
        ValueError: `base_url` is empty.
    """

    def __init__(
        self,
        base_url: str,
        *,
        username: str = "",
        secret: str = "",
        timeout: float = _DEFAULT_TIMEOUT,
        transport: SyncTransport | None = None,
    ) -> None:
        """Initialize the client."""
        _validate_options(base_url, username, secret, timeout)
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._secret = secret
        self._timeout = timeout
        self._transport: SyncTransport = transport or DEFAULT_SYNC_TRANSPORT

    def learn_spam(self, raw_mime: bytes | bytearray | memoryview) -> LearningResult:
        """Teach the spam filter that `raw_mime` is spam."""
        return self.learn(Disposition.SPAM, raw_mime)

    def learn_ham(self, raw_mime: bytes | bytearray | memoryview) -> LearningResult:
        """Teach the spam filter that `raw_mime` is not spam."""
        return self.learn(Disposition.HAM, raw_mime)

    def learn(
        self, disposition: Disposition, raw_mime: bytes | bytearray | memoryview
    ) -> LearningResult:
        """Submit one training example.

        Args:
            disposition: The training label.
            raw_mime: The exact raw RFC 822 bytes mxRaven processed; the
                service matches them against stored evidence by SHA-256.

        Returns:
            The service's learning result.

        Raises:
            ValueError: Credentials are missing or the disposition is invalid.
            FeedbackError: The service returned a non-success response.
        """
        if not self._username or not self._secret:
            message = "feedback: credentials are required for learning"
            raise ValueError(message)
        resolved = _require_disposition(disposition)
        endpoint = f"{self._base_url}/v1/feedback/learn/{resolved}"
        headers = {
            "Content-Type": _LEARN_CONTENT_TYPE,
            "Authorization": _basic_auth(self._username, self._secret),
        }
        try:
            response = self._transport.request(
                "POST",
                endpoint,
                headers=headers,
                body=bytes(raw_mime),
                timeout=self._timeout,
            )
        except Exception as error:
            message = "feedback: submit learning request"
            raise FeedbackError(0, message) from error
        return _parse_learning(response)

    def unsubscribe(self, token: str) -> None:
        """Perform an RFC 8058 one-click unsubscribe for `token`.

        Args:
            token: The unsubscribe token from the ``List-Unsubscribe`` URL.

        Raises:
            ValueError: The token is empty.
            FeedbackError: The service returned a non-success response.
        """
        value = token.strip()
        if value == "":
            message = "feedback: unsubscribe token is empty"
            raise ValueError(message)
        endpoint = f"{self._base_url}/v1/feedback/unsubscribe/{quote(value, safe='')}"
        try:
            response = self._transport.request(
                "POST",
                endpoint,
                headers={"Content-Type": _UNSUBSCRIBE_CONTENT_TYPE},
                body=_UNSUBSCRIBE_BODY,
                timeout=self._timeout,
            )
        except Exception as error:
            message = "feedback: submit unsubscribe request"
            raise FeedbackError(0, message) from error
        _require_success(response)


class AsyncClient:
    """Calls the mxRaven feedback service using asyncio.

    Args:
        base_url: The feedback service base URL.
        username: The submission API key username, used for learning.
        secret: The submission API key secret, used for learning.
        timeout: The request timeout in seconds. Defaults to ``30``.
        transport: An optional asynchronous HTTP transport.

    Raises:
        ValueError: `base_url` is empty.
    """

    def __init__(
        self,
        base_url: str,
        *,
        username: str = "",
        secret: str = "",
        timeout: float = _DEFAULT_TIMEOUT,
        transport: AsyncTransport | None = None,
    ) -> None:
        """Initialize the client."""
        _validate_options(base_url, username, secret, timeout)
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._secret = secret
        self._timeout = timeout
        self._transport: AsyncTransport = transport or DEFAULT_ASYNC_TRANSPORT

    async def learn_spam(self, raw_mime: bytes | bytearray | memoryview) -> LearningResult:
        """Teach the spam filter that `raw_mime` is spam."""
        return await self.learn(Disposition.SPAM, raw_mime)

    async def learn_ham(self, raw_mime: bytes | bytearray | memoryview) -> LearningResult:
        """Teach the spam filter that `raw_mime` is not spam."""
        return await self.learn(Disposition.HAM, raw_mime)

    async def learn(
        self, disposition: Disposition, raw_mime: bytes | bytearray | memoryview
    ) -> LearningResult:
        """Submit one training example asynchronously."""
        if not self._username or not self._secret:
            message = "feedback: credentials are required for learning"
            raise ValueError(message)
        resolved = _require_disposition(disposition)
        endpoint = f"{self._base_url}/v1/feedback/learn/{resolved}"
        headers = {
            "Content-Type": _LEARN_CONTENT_TYPE,
            "Authorization": _basic_auth(self._username, self._secret),
        }
        try:
            response = await self._transport.request(
                "POST",
                endpoint,
                headers=headers,
                body=bytes(raw_mime),
                timeout=self._timeout,
            )
        except Exception as error:
            message = "feedback: submit learning request"
            raise FeedbackError(0, message) from error
        return _parse_learning(response)

    async def unsubscribe(self, token: str) -> None:
        """Perform an RFC 8058 one-click unsubscribe for `token`."""
        value = token.strip()
        if value == "":
            message = "feedback: unsubscribe token is empty"
            raise ValueError(message)
        endpoint = f"{self._base_url}/v1/feedback/unsubscribe/{quote(value, safe='')}"
        try:
            response = await self._transport.request(
                "POST",
                endpoint,
                headers={"Content-Type": _UNSUBSCRIBE_CONTENT_TYPE},
                body=_UNSUBSCRIBE_BODY,
                timeout=self._timeout,
            )
        except Exception as error:
            message = "feedback: submit unsubscribe request"
            raise FeedbackError(0, message) from error
        _require_success(response)


def _validate_options(base_url: str, username: str, secret: str, timeout: float) -> None:
    if not base_url.strip():
        message = "feedback: base URL is required"
        raise ValueError(message)
    if bool(username) != bool(secret):
        message = "feedback: username and secret must both be provided"
        raise ValueError(message)
    if timeout <= 0:
        message = "feedback: timeout must be positive"
        raise ValueError(message)


def _require_disposition(disposition: Disposition) -> Disposition:
    try:
        return Disposition(disposition)
    except ValueError as error:
        message = f"feedback: invalid disposition {disposition!r}"
        raise ValueError(message) from error


def _basic_auth(username: str, secret: str) -> str:
    encoded = base64.b64encode(f"{username}:{secret}".encode()).decode("ascii")
    return f"Basic {encoded}"


def _parse_learning(response: HttpResponse) -> LearningResult:
    _require_success(response)
    try:
        data = json.loads(response.body[:_MAX_ERROR_BODY])
    except json.JSONDecodeError as error:
        message = "feedback: decode learning response"
        raise FeedbackError(response.status, message) from error
    if not isinstance(data, dict):
        message = "feedback: decode learning response"
        raise FeedbackError(response.status, message)
    return LearningResult(
        status=_field(data, "status"),
        disposition=_field(data, "disposition"),
        tenant_id=_field(data, "tenant_id"),
        listener_id=_field(data, "listener_id"),
        matched_hash_kind=_field(data, "matched_hash_kind"),
    )


def _require_success(response: HttpResponse) -> None:
    if not _HTTP_OK <= response.status < _HTTP_SUCCESS_MAX:
        raise _error(response)


def _error(response: HttpResponse) -> FeedbackError:
    message = response.reason
    try:
        payload = json.loads(response.body[:_MAX_ERROR_BODY])
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        value = payload.get("error")
        if isinstance(value, str) and value.strip():
            message = value.strip()
    return FeedbackError(response.status, message)


def _field(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    return value if isinstance(value, str) else ""
