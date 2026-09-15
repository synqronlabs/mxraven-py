"""Tests for the asynchronous submission client."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

from mxraven import Envelope, Message, SMTPTransactionError
from mxraven.aio import Client
from tests.fake_smtp import FakeSMTPConfig, FakeSMTPServer, running_server


def make_client(server: FakeSMTPServer) -> Client:
    return Client(
        "localhost",
        port=server.port,
        username="user",
        secret="pass",
        starttls=False,
        require_tls=False,
    )


def message() -> Message:
    return (
        Message()
        .from_("Acme <noreply@acme.example>")
        .to("customer@example.com")
        .subject("Your receipt")
        .text("Thanks for your order.")
    )


def test_async_send() -> None:
    async def run(server: FakeSMTPServer):
        async with make_client(server) as client:
            return await client.send(message())

    with running_server() as server:
        result = asyncio.run(run(server))
    assert result.message_ref == "ref-1"
    assert result.recipients[0].accepted is True
    assert server.messages[0].recipients == ["customer@example.com"]


def test_async_send_raw() -> None:
    async def run(server: FakeSMTPServer):
        async with make_client(server) as client:
            envelope = Envelope(sender="bounce@example.com", recipients=("customer@example.com",))
            return await client.send_raw(envelope, b"From: bounce@example.com\r\n\r\nbody")

    with running_server() as server:
        result = asyncio.run(run(server))
    assert result.message_ref == "ref-1"
    assert server.messages[0].envelope_sender == "bounce@example.com"


def test_async_all_rejected() -> None:
    config = FakeSMTPConfig(reject_recipients={"blocked@example.com"})

    async def run(server: FakeSMTPServer) -> None:
        async with make_client(server) as client:
            blocked = Message().from_("noreply@acme.example").to("blocked@example.com").text("hi")
            await client.send(blocked)

    with running_server(config) as server, pytest.raises(SMTPTransactionError):
        asyncio.run(run(server))


def test_async_send_raw_async_iterable() -> None:
    async def chunks() -> AsyncGenerator[bytes]:
        yield b"Subject: Split\r\n\r\n."
        yield b"\r\nbody ends with dot"
        yield b".\r\n."

    async def run(server: FakeSMTPServer):
        async with make_client(server) as client:
            envelope = Envelope(sender="sender@example.com", recipients=("rcpt@example.net",))
            return await client.send_raw(envelope, chunks())

    with running_server() as server:
        result = asyncio.run(run(server))
    assert result.recipients[0].accepted is True
    assert server.data_lines == ["Subject: Split", "", "..", "body ends with dot.", ".."]


def test_async_pool_reuses_connection() -> None:
    async def run(server: FakeSMTPServer) -> None:
        async with make_client(server) as client:
            await client.send(message())
            await client.send(message())

    with running_server() as server:
        asyncio.run(run(server))
    assert server.connections == 1
