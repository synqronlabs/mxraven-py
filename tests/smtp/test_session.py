"""Tests for the synchronous SMTP session against the in-process fake server."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import ssl

from mxraven._internal.address import parse_address
from mxraven._internal.smtp.driver import ConnectionConfig
from mxraven._internal.smtp.session import Session
from mxraven._internal.smtp.transaction import Recipient, Transaction
from mxraven.errors import (
    SMTPCapabilityError,
    SMTPConnectionError,
    SMTPError,
    SMTPTimeoutError,
    SMTPTransactionError,
)
from mxraven.message import BuiltMessage, Message
from tests.fake_smtp import FakeSMTPConfig, FakeSMTPServer, make_tls_pair, running_server


def build_message(*, to: tuple[str, ...] = ("customer@example.com",)) -> BuiltMessage:
    return (
        Message()
        .from_("Acme <noreply@acme.example>")
        .to(*to)
        .subject("Your receipt")
        .text("Thanks for your order.")
        .build()
    )


def transaction_for(built: BuiltMessage) -> Transaction:
    sender = parse_address(built.sender) if built.sender else None
    return Transaction(
        sender=sender,
        recipients=tuple(Recipient(parse_address(address)) for address in built.recipients),
        size=built.size,
        body_type=built.body_type,
        smtp_utf8=built.smtp_utf8,
    )


def config_for(
    server: FakeSMTPServer,
    *,
    starttls: bool = False,
    require_tls: bool = False,
    tls_context: ssl.SSLContext | None = None,
    username: str = "user",
    password: str = "pass",
    implicit_tls: bool = False,
    local_address: str | None = None,
    auth_mechanisms: tuple[str, ...] = (),
    read_timeout: float = 300.0,
    local_name: str = "localhost",
) -> ConnectionConfig:
    base = ConnectionConfig(
        host="localhost",
        port=server.port,
        username=username,
        password=password,
    )
    return replace(
        base,
        starttls=starttls,
        require_tls=require_tls,
        tls_context=tls_context,
        implicit_tls=implicit_tls,
        local_address=local_address,
        auth_mechanisms=auth_mechanisms,
        read_timeout=read_timeout,
        local_name=local_name,
    )


def test_send_simple_message() -> None:
    with running_server() as server:
        session = Session(config_for(server))
        session.connect()
        built = build_message()
        result = session.send(transaction_for(built), built.data)
        session.close()

    assert result.message_ref == "ref-1"
    assert result.code == 250
    assert len(result.recipients) == 1
    assert result.recipients[0].accepted is True
    assert server.messages[0].recipients == ["customer@example.com"]
    assert b"Thanks for your order." in server.messages[0].data
    assert any(command.startswith("EHLO") for command in server.commands)
    assert any(command.startswith("AUTH PLAIN") for command in server.commands)
    assert "QUIT" in server.commands


def test_ehlo_falls_back_to_helo() -> None:
    with running_server(FakeSMTPConfig(reject_ehlo=True)) as server:
        session = Session(config_for(server, username="", password=""))
        session.connect()
        capabilities = session.state.capabilities
        session.close()
    assert any(command.startswith("HELO") for command in server.commands)
    assert capabilities is not None
    assert capabilities.is_esmtp is False


def test_starttls_required_but_missing() -> None:
    extensions = ["PIPELINING", "AUTH PLAIN LOGIN"]
    with running_server(FakeSMTPConfig(extensions=extensions)) as server:
        session = Session(config_for(server, starttls=True, require_tls=True))
        with pytest.raises(SMTPCapabilityError):
            session.connect()


@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
def test_starttls_upgrade_and_send() -> None:
    server_context, client_context = make_tls_pair()
    config = FakeSMTPConfig(tls_context=server_context)
    with running_server(config) as server:
        session = Session(
            config_for(server, starttls=True, require_tls=True, tls_context=client_context)
        )
        session.connect()
        assert session.state.is_tls is True
        built = build_message()
        result = session.send(transaction_for(built), built.data)
        session.close()
    assert result.message_ref == "ref-1"


def test_all_recipients_rejected() -> None:
    config = FakeSMTPConfig(reject_recipients={"blocked@example.com"})
    with running_server(config) as server:
        session = Session(config_for(server))
        session.connect()
        built = build_message(to=("blocked@example.com",))
        with pytest.raises(SMTPTransactionError) as info:
            session.send(transaction_for(built), built.data)
        session.close()

    assert info.value.result.recipients[0].accepted is False
    assert "RSET" in server.commands
    assert server.messages == []


def test_partial_acceptance() -> None:
    config = FakeSMTPConfig(reject_recipients={"bad@example.com"})
    with running_server(config) as server:
        session = Session(config_for(server))
        session.connect()
        built = build_message(to=("good@example.com", "bad@example.com"))
        result = session.send(transaction_for(built), built.data)
        session.close()

    assert [recipient.accepted for recipient in result.recipients] == [True, False]
    assert result.recipients[1].error is not None


def test_data_dot_stuffing() -> None:
    with running_server() as server:
        session = Session(config_for(server))
        session.connect()
        built = build_message()
        content = b"Subject: Raw\r\n\r\n.line\r\nbody without final newline"
        session.send(transaction_for(built), content)
        session.close()
    data = server.messages[0].data
    assert data == content.replace(b"\r\n.line", b"\r\n..line")


def test_bdat_is_used_when_requested() -> None:
    extensions = ["PIPELINING", "CHUNKING", "AUTH PLAIN LOGIN", "DSN"]
    with running_server(FakeSMTPConfig(extensions=extensions)) as server:
        session = Session(config_for(server))
        session.connect()
        built = build_message()
        session.send(transaction_for(built), built.data, prefer_bdat=True)
        session.close()
    assert server.bdat_chunks
    assert "DATA" not in server.commands
    assert b"Thanks for your order." in server.messages[0].data


def test_pipelined_envelope() -> None:
    with running_server() as server:
        session = Session(config_for(server))
        session.connect()
        built = build_message(to=("a@example.com", "b@example.com"))
        result = session.send(transaction_for(built), built.data)
        session.close()
    assert [recipient.accepted for recipient in result.recipients] == [True, True]
    assert server.messages[0].recipients == ["a@example.com", "b@example.com"]


def test_sequential_envelope_without_pipelining() -> None:
    extensions = ["8BITMIME", "SMTPUTF8", "AUTH PLAIN LOGIN"]
    with running_server(FakeSMTPConfig(extensions=extensions)) as server:
        session = Session(config_for(server))
        session.connect()
        built = build_message(to=("a@example.com", "b@example.com"))
        result = session.send(transaction_for(built), built.data)
        session.close()
    assert [recipient.accepted for recipient in result.recipients] == [True, True]


def test_auth_login() -> None:
    extensions = ["PIPELINING", "AUTH LOGIN"]
    with running_server(FakeSMTPConfig(extensions=extensions)) as server:
        session = Session(config_for(server))
        session.connect()
        assert session.state.authenticated is True
        session.close()
    assert "AUTH LOGIN" in server.commands


def test_auth_preferred_mechanism() -> None:
    with running_server() as server:
        session = Session(config_for(server, auth_mechanisms=("LOGIN",)))
        session.connect()
        session.close()
    assert "AUTH LOGIN" in server.commands


def test_auth_failure() -> None:
    server_config = FakeSMTPConfig(username="user", password="pass")
    with (
        running_server(server_config) as server,
        pytest.raises(SMTPError) as info,
    ):
        Session(config_for(server, password="wrong")).connect()
    assert info.value.code == 535


def test_auth_extension_missing() -> None:
    extensions = ["PIPELINING", "8BITMIME"]
    with (
        running_server(FakeSMTPConfig(extensions=extensions)) as server,
        pytest.raises(SMTPCapabilityError),
    ):
        Session(config_for(server)).connect()


def test_starttls_optional_and_absent_proceeds_plaintext() -> None:
    extensions = ["PIPELINING", "AUTH PLAIN LOGIN"]
    with running_server(FakeSMTPConfig(extensions=extensions)) as server:
        session = Session(config_for(server, starttls=True, require_tls=False))
        session.connect()
        assert session.state.is_tls is False
        built = build_message()
        session.send(transaction_for(built), built.data)
        session.close()
    assert "STARTTLS" not in server.commands


def test_bad_greeting() -> None:
    with (
        running_server(FakeSMTPConfig(greeting="554 no service here")) as server,
        pytest.raises(SMTPError) as info,
    ):
        Session(config_for(server)).connect()
    assert info.value.code == 554


@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
def test_read_timeout() -> None:
    with (
        running_server(FakeSMTPConfig(hang=2.0)) as server,
        pytest.raises(SMTPTimeoutError),
    ):
        Session(config_for(server, read_timeout=0.1)).connect()


def test_connection_closed_after_greeting() -> None:
    with (
        running_server(FakeSMTPConfig(close_after_greeting=True)) as server,
        pytest.raises(SMTPConnectionError),
    ):
        Session(config_for(server)).connect()


def test_pipelined_mail_rejection_drains_replies() -> None:
    with running_server(FakeSMTPConfig(reject_mail_from=True)) as server:
        session = Session(config_for(server))
        session.connect()
        built = build_message(to=("a@example.com", "b@example.com"))
        with pytest.raises(SMTPError):
            session.send(transaction_for(built), built.data)
        session.noop()
        session.close()


def test_bdat_chunked() -> None:
    extensions = ["PIPELINING", "CHUNKING", "AUTH PLAIN LOGIN"]
    with running_server(FakeSMTPConfig(extensions=extensions)) as server:
        session = Session(config_for(server))
        session.connect()
        built = build_message()
        result = session.send(transaction_for(built), b"x" * 70000, prefer_bdat=True)
        session.close()
    assert len(server.bdat_chunks) >= 2
    assert sum(server.bdat_chunks) == 70000
    assert "DATA" not in server.commands
    assert result.message_ref == ""


def test_bdat_empty_payload() -> None:
    extensions = ["PIPELINING", "CHUNKING", "AUTH PLAIN LOGIN"]
    with running_server(FakeSMTPConfig(extensions=extensions)) as server:
        session = Session(config_for(server))
        session.connect()
        built = build_message()
        session.send(transaction_for(built), b"", prefer_bdat=True)
        session.close()
    assert server.bdat_chunks == [0]
    assert server.messages[0].data == b""


def test_auto_bdat_for_large_message() -> None:
    extensions = ["PIPELINING", "CHUNKING", "AUTH PLAIN LOGIN"]
    with running_server(FakeSMTPConfig(extensions=extensions)) as server:
        session = Session(config_for(server))
        session.connect()
        built = build_message()
        session.send(transaction_for(built), b"x" * (1024 * 1024 + 1))
        session.close()
    assert server.bdat_chunks == [1024 * 1024 + 1]
    assert "DATA" not in server.commands


def test_implicit_tls() -> None:
    server_context, client_context = make_tls_pair()
    config = FakeSMTPConfig(tls_context=server_context, implicit_tls=True)
    with running_server(config) as server:
        session = Session(
            config_for(
                server,
                implicit_tls=True,
                tls_context=client_context,
                starttls=False,
                require_tls=False,
            )
        )
        session.connect()
        assert session.state.is_tls is True
        built = build_message()
        result = session.send(transaction_for(built), built.data)
        session.close()
    assert "STARTTLS" not in server.commands
    assert result.message_ref == "ref-1"


def test_local_address_binding() -> None:
    with running_server() as server:
        session = Session(config_for(server, local_address="127.0.0.1:0"))
        session.connect()
        built = build_message()
        result = session.send(transaction_for(built), built.data)
        session.close()
    assert result.message_ref == "ref-1"


def test_local_address_host_only() -> None:
    with running_server() as server:
        session = Session(config_for(server, local_address="127.0.0.1"))
        session.connect()
        assert session.state.capabilities is not None
        session.close()


def test_invalid_local_address() -> None:
    with running_server() as server:
        session = Session(config_for(server, local_address="invalid.invalid"))
        with pytest.raises(SMTPConnectionError):
            session.connect()


def test_send_without_connection() -> None:
    with running_server() as server:
        session = Session(config_for(server))
        built = build_message()
        with pytest.raises(SMTPConnectionError):
            session.send(transaction_for(built), built.data)


def test_connect_after_close_is_rejected() -> None:
    with running_server() as server:
        session = Session(config_for(server))
        session.connect()
        session.close()
        with pytest.raises(SMTPConnectionError, match="closed"):
            session.connect()


def test_pipelining_retained_after_starttls() -> None:
    server_context, client_context = make_tls_pair()
    config = FakeSMTPConfig(tls_context=server_context)
    with running_server(config) as server:
        session = Session(
            config_for(server, starttls=True, require_tls=True, tls_context=client_context)
        )
        session.connect()
        capabilities = session.state.capabilities
        session.close()
    assert capabilities is not None
    assert capabilities.pipelining is True


def test_tls_server_name_mismatch() -> None:
    server_context, client_context = make_tls_pair("other.example.com")
    config = FakeSMTPConfig(tls_context=server_context, implicit_tls=True)
    with running_server(config) as server:
        session = Session(
            config_for(
                server,
                implicit_tls=True,
                tls_context=client_context,
                starttls=False,
                require_tls=False,
            )
        )
        with pytest.raises(SMTPConnectionError):
            session.connect()


def test_implicit_tls_connection_failure() -> None:
    config = ConnectionConfig(
        host="127.0.0.1",
        port=1,
        implicit_tls=True,
        connect_timeout=0.5,
    )
    with pytest.raises(SMTPConnectionError):
        Session(config).connect()


def test_config_defaults() -> None:
    config = ConnectionConfig(host="mail.example.com", port=465)
    assert config.local_name == "localhost"
    assert config.connect_timeout == 30.0
    assert config.read_timeout == 300.0
    assert config.write_timeout == 300.0


def test_local_name_used_in_ehlo() -> None:
    with running_server() as server:
        session = Session(config_for(server, local_name="my.client.example.com"))
        session.connect()
        session.close()
    assert "EHLO my.client.example.com" in server.commands


def test_require_all_recipients() -> None:
    config = FakeSMTPConfig(reject_recipients={"bad@example.com"})
    with running_server(config) as server:
        session = Session(config_for(server))
        session.connect()
        built = build_message(to=("good@example.com", "bad@example.com"))
        try:
            with pytest.raises(SMTPTransactionError):
                session.send(transaction_for(built), built.data, require_all_recipients=True)
        finally:
            session.close()
    assert server.rset_commands >= 1


def test_data_used_at_exactly_threshold() -> None:
    extensions = ["PIPELINING", "CHUNKING", "AUTH PLAIN LOGIN"]
    with running_server(FakeSMTPConfig(extensions=extensions)) as server:
        session = Session(config_for(server))
        session.connect()
        built = build_message()
        session.send(transaction_for(built), b"x" * (1024 * 1024))
        session.close()
    assert server.data_commands == 1
    assert server.bdat_commands == 0
