"""Tests for the public submission client."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import ssl
    from collections.abc import Iterator

from mxraven import (
    Client,
    DeliveryBy,
    DeliveryByMode,
    Envelope,
    Message,
    SMTPCapabilityError,
    SMTPConnectionError,
    SMTPError,
    SMTPTransactionError,
)
from tests.fake_smtp import FakeSMTPConfig, FakeSMTPServer, make_tls_pair, running_server

_BASE_EXTENSIONS = [
    "PIPELINING",
    "8BITMIME",
    "SMTPUTF8",
    "ENHANCEDSTATUSCODES",
    "DSN",
    "SIZE 10485760",
]


def tls_client_for(server: FakeSMTPServer, client_context: ssl.SSLContext) -> Client:
    return Client(
        "localhost",
        port=server.port,
        username="user",
        secret="pass",
        tls=client_context,
        starttls=True,
        require_tls=True,
    )


def client_for(server: FakeSMTPServer) -> Client:
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


@pytest.mark.parametrize(
    "kwargs",
    [
        {"host": "", "username": "u", "secret": "s"},
        {"host": "h", "port": 0, "username": "u", "secret": "s"},
        {"host": "h", "port": 70000, "username": "u", "secret": "s"},
        {"host": "h", "username": "", "secret": "s"},
        {"host": "h", "username": "u", "secret": ""},
        {"host": "h", "username": "u", "secret": "s", "pool_size": 0},
        {"host": "h", "username": "u", "secret": "s", "connect_timeout": 0},
    ],
)
def test_constructor_validation(kwargs: dict[str, object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        Client(**kwargs)  # type: ignore[arg-type]


def test_send_returns_result() -> None:
    with running_server() as server, client_for(server) as client:
        result = client.send(message())
    assert result.message_ref == "ref-1"
    assert result.recipients[0].accepted is True
    assert server.messages[0].recipients == ["customer@example.com"]


def test_send_raw() -> None:
    with running_server() as server, client_for(server) as client:
        envelope = Envelope(sender="bounce@example.com", recipients=("customer@example.com",))
        result = client.send_raw(envelope, b"From: bounce@example.com\r\n\r\nbody")
    assert result.message_ref == "ref-1"
    assert server.messages[0].envelope_sender == "bounce@example.com"


def test_send_raw_requires_recipients() -> None:
    with (
        running_server() as server,
        client_for(server) as client,
        pytest.raises(ValueError, match="at least one recipient"),
    ):
        client.send_raw(Envelope(sender="a@example.com"), b"body")


def test_all_recipients_rejected() -> None:
    config = FakeSMTPConfig(reject_recipients={"blocked@example.com"})
    with (
        running_server(config) as server,
        client_for(server) as client,
        pytest.raises(SMTPTransactionError) as info,
    ):
        blocked = Message().from_("noreply@acme.example").to("blocked@example.com").text("hi")
        client.send(blocked)
    assert info.value.result.recipients[0].accepted is False


def test_send_requires_message() -> None:
    with (
        running_server() as server,
        client_for(server) as client,
        pytest.raises(TypeError, match="message is required"),
    ):
        client.send("not a message")  # type: ignore[arg-type]


def test_pool_reuses_connection() -> None:
    with running_server() as server, client_for(server) as client:
        client.send(message())
        client.send(message())
    assert server.connections == 1


def test_close_is_idempotent() -> None:
    with running_server() as server:
        client = client_for(server)
        client.close()
        client.close()


def test_internationalized_message_advertises_size_and_smtputf8() -> None:
    with running_server() as server, client_for(server) as client:
        client.send(
            Message().from_("üser@acme.example").to("customer@example.com").subject("Hi").text("Hi")
        )
    line = server.mail_from_lines[0]
    assert "SIZE=" in line
    assert "SMTPUTF8" in line


def test_concurrent_sends_use_separate_connections() -> None:
    with running_server() as server, client_for(server) as client:
        barrier = threading.Barrier(2)

        def submit(recipient: str) -> None:
            barrier.wait()
            client.send(message().to(recipient))

        threads = [
            threading.Thread(target=submit, args=("one@example.com",)),
            threading.Thread(target=submit, args=("two@example.com",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
    assert server.connections == 2


def test_closed_client_rejects_send() -> None:
    with running_server() as server:
        client = client_for(server)
        client.close()
        with pytest.raises(SMTPConnectionError, match="closed"):
            client.send(message())


def test_surfaces_connection_failure() -> None:
    client = Client(
        "127.0.0.1", port=1, username="u", secret="s", connect_timeout=0.5, starttls=False
    )
    with pytest.raises(SMTPConnectionError):
        client.send(message())
    client.close()


def test_send_raw_invalid_envelope_address() -> None:
    with (
        running_server() as server,
        client_for(server) as client,
        pytest.raises(ValueError, match="mail:"),
    ):
        client.send_raw(Envelope(sender="not an address", recipients=("a@example.com",)), b"x")


def test_send_raw_dot_stuffs() -> None:
    with running_server() as server, client_for(server) as client:
        client.send_raw(
            Envelope(sender="sender@example.com", recipients=("rcpt@example.net",)),
            b"Subject: Raw\r\n\r\n.body",
        )
    assert "..body" in server.data_lines


def test_send_raw_streams_chunked_data() -> None:
    def chunks() -> Iterator[bytes]:
        yield b"Subject: Split\r\n\r\n."
        yield b"\r\nbody ends with dot"
        yield b".\r\n."

    with running_server() as server, client_for(server) as client:
        result = client.send_raw(
            Envelope(sender="sender@example.com", recipients=("rcpt@example.net",)),
            chunks(),
        )
    assert result.recipients[0].accepted is True
    assert server.data_lines == ["Subject: Split", "", "..", "body ends with dot.", ".."]


def test_deliver_by_sent_when_advertised() -> None:
    extensions = [*_BASE_EXTENSIONS, "AUTH PLAIN LOGIN", "DELIVERBY 300"]
    with (
        running_server(FakeSMTPConfig(extensions=extensions)) as server,
        client_for(server) as client,
    ):
        client.send(message().deliver_by(DeliveryBy(600, DeliveryByMode.RETURN)))
    assert "BY=600;R" in server.mail_from_lines[0]


def test_deliver_by_unsupported_raises() -> None:
    with (
        running_server() as server,
        client_for(server) as client,
        pytest.raises(SMTPCapabilityError),
    ):
        client.send(message().deliver_by(DeliveryBy(600, DeliveryByMode.RETURN)))


def test_deliver_by_below_minimum_raises() -> None:
    extensions = [*_BASE_EXTENSIONS, "AUTH PLAIN LOGIN", "DELIVERBY 300"]
    with (
        running_server(FakeSMTPConfig(extensions=extensions)) as server,
        client_for(server) as client,
        pytest.raises(ValueError, match="below server minimum"),
    ):
        client.send(message().deliver_by(DeliveryBy(100, DeliveryByMode.RETURN)))


def test_send_raw_envelope_deliver_by() -> None:
    extensions = [*_BASE_EXTENSIONS, "AUTH PLAIN LOGIN", "DELIVERBY 300"]
    with (
        running_server(FakeSMTPConfig(extensions=extensions)) as server,
        client_for(server) as client,
    ):
        envelope = Envelope(
            sender="sender@example.com",
            recipients=("rcpt@example.net",),
            delivery_by=DeliveryBy(600, DeliveryByMode.RETURN),
        )
        client.send_raw(envelope, b"Subject: Raw\r\n\r\nbody")
    assert "BY=600;R" in server.mail_from_lines[0]


def test_require_tls_requires_encrypted_session() -> None:
    with (
        running_server() as server,
        client_for(server) as client,
        pytest.raises(SMTPError) as info,
    ):
        client.send(message().require_tls())
    assert info.value.enhanced_code == "5.7.30"


def test_require_tls_requires_server_support() -> None:
    server_context, client_context = make_tls_pair()
    config = FakeSMTPConfig(
        extensions=[*_BASE_EXTENSIONS, "AUTH PLAIN LOGIN", "STARTTLS"],
        tls_context=server_context,
    )
    with (
        running_server(config) as server,
        tls_client_for(server, client_context) as client,
        pytest.raises(SMTPError, match="support required"),
    ):
        client.send(message().require_tls())


def test_require_tls_sent_over_starttls() -> None:
    server_context, client_context = make_tls_pair()
    config = FakeSMTPConfig(
        extensions=[*_BASE_EXTENSIONS, "AUTH PLAIN LOGIN", "STARTTLS", "REQUIRETLS"],
        tls_context=server_context,
    )
    with running_server(config) as server, tls_client_for(server, client_context) as client:
        result = client.send(message().require_tls())
    assert result.message_ref == "ref-1"
    assert "REQUIRETLS" in server.mail_from_lines[0]
