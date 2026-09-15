"""Tests for the bounded connection pool, ported from raven and mxraven-js."""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import replace

import pytest

from mxraven._internal.address import parse_address
from mxraven._internal.smtp.driver import ConnectionConfig
from mxraven._internal.smtp.pool import Pool
from mxraven._internal.smtp.session import Session
from mxraven._internal.smtp.transaction import Recipient, Transaction
from mxraven.errors import SMTPConnectionError, SMTPTransactionError
from mxraven.message import BuiltMessage, Message
from tests.fake_smtp import FakeSMTPConfig, FakeSMTPServer, running_server


def config_for(server: FakeSMTPServer) -> ConnectionConfig:
    base = ConnectionConfig(host="localhost", port=server.port, username="user", password="pass")
    return replace(base, starttls=False, require_tls=False)


def build_message() -> BuiltMessage:
    return Message().from_("a@example.com").to("b@example.com").text("hi").build()


def transaction_for(built: BuiltMessage) -> Transaction:
    return Transaction(
        sender=parse_address(built.sender),
        recipients=tuple(Recipient(parse_address(r)) for r in built.recipients),
        size=built.size,
        smtp_utf8=built.smtp_utf8,
    )


@pytest.mark.parametrize("size", [0, -1])
def test_non_positive_size_defaults_to_five(size: int) -> None:
    with running_server() as server:
        assert Pool(config_for(server), size).size == 5


def test_custom_size() -> None:
    with running_server() as server:
        assert Pool(config_for(server), 10).size == 10


def test_is_closed() -> None:
    with running_server() as server:
        pool = Pool(config_for(server), 1)
        assert pool.is_closed is False
        pool.close()
        assert pool.is_closed is True


def test_release_foreign_session_is_ignored() -> None:
    with running_server() as server:
        pool = Pool(config_for(server), 1)
        foreign = Session(config_for(server))
        pool.release(foreign)
        assert pool.is_closed is False
        pool.close()


def test_closes_sessions_released_while_closing() -> None:
    with running_server() as server:
        pool = Pool(config_for(server), 2)
        first = pool.acquire()
        second = pool.acquire()
        barrier = threading.Barrier(3)

        def release(session: Session) -> None:
            barrier.wait()
            pool.release(session)

        threads = [
            threading.Thread(target=release, args=(first,)),
            threading.Thread(target=release, args=(second,)),
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        pool.close()
        for thread in threads:
            thread.join(timeout=5)
        with pytest.raises(SMTPConnectionError):
            first.noop()
        with pytest.raises(SMTPConnectionError):
            second.noop()


def test_discards_stale_idle_session() -> None:
    with running_server() as server:
        pool = Pool(config_for(server), 2)
        first = pool.acquire()
        pool.release(first)
        first.discard()
        second = pool.acquire()
        assert second is not first
        pool.release(second)
        pool.close()


def test_acquire_release_reuses_connection() -> None:
    with running_server() as server:
        pool = Pool(config_for(server), 2)
        first = pool.acquire()
        pool.release(first)
        second = pool.acquire()
        assert second is first
        second.discard()
        pool.close()
    assert server.connections == 1


def test_send_failure_discards_connection() -> None:
    config = FakeSMTPConfig(reject_recipients={"blocked@example.com"})
    with running_server(config) as server:
        pool = Pool(config_for(server), 2)
        blocked = Message().from_("a@example.com").to("blocked@example.com").text("hi").build()
        with pytest.raises(SMTPTransactionError):
            pool.send(transaction_for(blocked), blocked.data)
        good = build_message()
        pool.send(transaction_for(good), good.data)
        pool.close()
    assert server.connections == 2


def test_dial_failure_releases_capacity() -> None:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    closed_port = sock.getsockname()[1]
    sock.close()
    config = ConnectionConfig(host="127.0.0.1", port=closed_port)
    pool = Pool(config, 1)
    for _ in range(2):
        with pytest.raises(SMTPConnectionError):
            pool.acquire(timeout=1.0)
    pool.close()


def test_close_wakes_blocked_acquire() -> None:
    with running_server() as server:
        pool = Pool(config_for(server), 1)
        held = pool.acquire()
        outcome: list[str] = []

        def worker() -> None:
            try:
                pool.acquire(timeout=5.0)
            except SMTPConnectionError:
                outcome.append("closed")

        thread = threading.Thread(target=worker)
        thread.start()
        time.sleep(0.1)
        pool.close()
        thread.join(timeout=5)
        held.discard()
    assert outcome == ["closed"]


def test_pool_bounds_checked_out_connections() -> None:
    with running_server() as server:
        pool = Pool(config_for(server), 1)
        first = pool.acquire()
        results: list[object] = []

        def worker() -> None:
            session = pool.acquire(timeout=5.0)
            results.append(session)
            pool.release(session)

        thread = threading.Thread(target=worker)
        thread.start()
        time.sleep(0.1)
        assert results == []  # the worker blocks until we release
        pool.release(first)
        thread.join(timeout=5)
        pool.close()
    assert len(results) == 1
    assert server.connections == 1


def test_acquire_after_close() -> None:
    with running_server() as server:
        pool = Pool(config_for(server), 1)
        pool.close()
        with pytest.raises(SMTPConnectionError, match="closed"):
            pool.acquire()
