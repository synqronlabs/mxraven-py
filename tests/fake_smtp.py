"""An in-process scriptable SMTP server for transport tests.

The server binds to an ephemeral port on ``127.0.0.1`` and never touches the
network. It supports EHLO/HELO, STARTTLS, AUTH PLAIN/LOGIN, PIPELINING, DSN,
DATA, and BDAT, and records everything a test needs to assert on.
"""

from __future__ import annotations

import base64
import contextlib
import socketserver
import ssl
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Self, cast

import trustme

if TYPE_CHECKING:
    from collections.abc import Iterator

_TERMINATOR = b".\r\n"
_BARE_LF_TERMINATOR = b".\n"
_CRLF = b"\r\n"


@dataclass
class FakeSMTPConfig:
    """Behavior of a :class:`FakeSMTPServer`."""

    greeting: str = "220 mock.example.com ESMTP"
    extensions: list[str] = field(
        default_factory=lambda: [
            "PIPELINING",
            "8BITMIME",
            "SMTPUTF8",
            "ENHANCEDSTATUSCODES",
            "DSN",
            "SIZE 10485760",
            "AUTH PLAIN LOGIN",
            "STARTTLS",
        ]
    )
    reject_ehlo: bool = False
    tls_context: ssl.SSLContext | None = None
    implicit_tls: bool = False
    hang: float = 0.0
    close_after_greeting: bool = False
    username: str = ""
    password: str = ""
    reject_mail_from: bool = False
    reject_recipients: set[str] = field(default_factory=set)
    data_reply: str = "250 2.0.0 accepted; message_ref=ref-1"


@dataclass
class ReceivedMessage:
    """A message captured by the server."""

    envelope_sender: str
    recipients: list[str]
    data: bytes


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], config: FakeSMTPConfig) -> None:
        super().__init__(address, _Handler)
        self.config = config
        self.messages: list[ReceivedMessage] = []
        self.commands: list[str] = []
        self.bdat_chunks: list[int] = []
        self.connections = 0


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        server = cast("_Server", self.server)
        server.connections += 1
        config = server.config
        sender = ""
        recipients: list[str] = []
        bdat_buffer = bytearray()
        if config.hang:
            time.sleep(config.hang)
        if config.implicit_tls and config.tls_context is not None:
            self.connection = config.tls_context.wrap_socket(self.connection, server_side=True)
            self.rfile = self.connection.makefile("rb")
            self.wfile = self.connection.makefile("wb")
        self._write_line(config.greeting)
        if config.close_after_greeting:
            return

        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            server.commands.append(line)
            verb, _, rest = line.partition(" ")
            verb = verb.upper()

            if verb in ("EHLO", "HELO"):
                self._handle_hello(config, verb)
            elif verb == "STARTTLS":
                self._handle_starttls(config)
            elif verb == "AUTH":
                self._handle_auth(config, rest)
            elif verb == "MAIL":
                sender = _extract_path(rest)
                self._write_line(
                    "550 5.1.0 sender rejected"
                    if config.reject_mail_from
                    else "250 2.1.0 sender ok"
                )
            elif verb == "RCPT":
                address = _extract_path(rest)
                if address in config.reject_recipients:
                    self._write_line("550 5.1.1 no such user")
                else:
                    recipients.append(address)
                    self._write_line("250 2.1.5 recipient ok")
            elif verb == "DATA":
                self._write_line("354 End data with <CR><LF>.<CR><LF>")
                server.messages.append(ReceivedMessage(sender, list(recipients), self._read_data()))
                self._write_line(config.data_reply)
                sender, recipients = "", []
            elif verb == "BDAT":
                self._handle_bdat(server, rest, bdat_buffer, sender, recipients)
                if bdat_buffer == bytearray():
                    sender, recipients = "", []
            elif verb == "RSET":
                sender, recipients = "", []
                self._write_line("250 2.0.0 ok")
            elif verb == "NOOP":
                self._write_line("250 2.0.0 ok")
            elif verb == "QUIT":
                self._write_line("221 2.0.0 bye")
                return
            else:
                self._write_line("500 5.5.1 command not recognized")

    def _handle_hello(self, config: FakeSMTPConfig, verb: str) -> None:
        if verb == "EHLO":
            if config.reject_ehlo:
                self._write_line("502 5.5.1 command not implemented")
                return
            self._write_line("250-mock.example.com Hello")
            for extension in config.extensions:
                self._write_line(f"250-{extension}")
            self._write_line("250 OK")
            return
        self._write_line("250 mock.example.com")

    def _handle_starttls(self, config: FakeSMTPConfig) -> None:
        if config.tls_context is None:
            self._write_line("454 TLS not available")
            return
        self._write_line("220 Ready to start TLS")
        self.connection = config.tls_context.wrap_socket(self.connection, server_side=True)
        self.rfile = self.connection.makefile("rb")
        self.wfile = self.connection.makefile("wb")

    def _handle_auth(self, config: FakeSMTPConfig, rest: str) -> None:
        mechanism, _, argument = rest.partition(" ")
        mechanism = mechanism.upper()
        if mechanism == "PLAIN":
            decoded = base64.b64decode(argument).decode("utf-8", errors="replace")
            parts = decoded.split("\x00")
            username = parts[-2] if len(parts) >= 2 else ""
            password = parts[-1]
        elif mechanism == "LOGIN":
            self._write_line("334 VXNlcm5hbWU6")
            username = base64.b64decode(self.rfile.readline().strip()).decode()
            self._write_line("334 UGFzc3dvcmQ6")
            password = base64.b64decode(self.rfile.readline().strip()).decode()
        else:
            self._write_line("504 5.5.4 unsupported mechanism")
            return

        if config.username and (username != config.username or password != config.password):
            self._write_line("535 5.7.8 authentication failed")
        else:
            self._write_line("235 2.7.0 authentication successful")

    def _handle_bdat(
        self,
        server: _Server,
        rest: str,
        buffer: bytearray,
        sender: str,
        recipients: list[str],
    ) -> None:
        size_text, _, flags = rest.partition(" ")
        size = int(size_text or "0")
        server.bdat_chunks.append(size)
        if size:
            buffer += self.rfile.read(size)
        if "LAST" in flags.upper():
            server.messages.append(ReceivedMessage(sender, list(recipients), bytes(buffer)))
            buffer.clear()
            self._write_line("250 2.0.0 accepted")
        else:
            self._write_line("250 2.0.0 chunk ok")

    def _read_data(self) -> bytes:
        lines: list[bytes] = []
        while True:
            raw = self.rfile.readline()
            if not raw:
                break
            if raw in (_TERMINATOR, _BARE_LF_TERMINATOR):
                break
            lines.append(raw[:-2] if raw.endswith(_CRLF) else raw)
        return _CRLF.join(lines)

    def finish(self) -> None:
        try:
            super().finish()
        finally:
            with contextlib.suppress(Exception):
                self.connection.close()

    def _write_line(self, line: str) -> None:
        self.wfile.write((line + "\r\n").encode("utf-8"))
        self.wfile.flush()


def _extract_path(rest: str) -> str:
    _, _, tail = rest.partition(":")
    return tail.split(" ", 1)[0].strip("<>")


class FakeSMTPServer:
    """A scriptable SMTP server bound to ``127.0.0.1`` on an ephemeral port."""

    def __init__(self, config: FakeSMTPConfig | None = None) -> None:
        """Create the server; call :meth:`start` to begin accepting connections."""
        self.config = config if config is not None else FakeSMTPConfig()
        self._server = _Server(("127.0.0.1", 0), self.config)
        self._thread: threading.Thread | None = None

    @property
    def host(self) -> str:
        """The bound host."""
        return "127.0.0.1"

    @property
    def port(self) -> int:
        """The bound port."""
        return self._server.server_address[1]

    @property
    def messages(self) -> list[ReceivedMessage]:
        """The messages the server received."""
        return self._server.messages

    @property
    def commands(self) -> list[str]:
        """The commands the server received."""
        return self._server.commands

    @property
    def bdat_chunks(self) -> list[int]:
        """The BDAT chunk sizes the server received."""
        return self._server.bdat_chunks

    @property
    def connections(self) -> int:
        """The number of connections the server accepted."""
        return self._server.connections

    @property
    def mail_from_lines(self) -> list[str]:
        """The ``MAIL FROM`` commands the server received."""
        return [command for command in self._server.commands if command.startswith("MAIL FROM:")]

    @property
    def rcpt_to_lines(self) -> list[str]:
        """The ``RCPT TO`` commands the server received."""
        return [command for command in self._server.commands if command.startswith("RCPT TO:")]

    @property
    def data_commands(self) -> int:
        """The number of ``DATA`` commands the server received."""
        return sum(1 for command in self._server.commands if command == "DATA")

    @property
    def bdat_commands(self) -> int:
        """The number of ``BDAT`` commands the server received."""
        return sum(1 for command in self._server.commands if command.startswith("BDAT"))

    @property
    def rset_commands(self) -> int:
        """The number of ``RSET`` commands the server received."""
        return sum(1 for command in self._server.commands if command == "RSET")

    @property
    def data_lines(self) -> list[str]:
        """The lines of the most recently received message."""
        if not self._server.messages:
            return []
        raw = self._server.messages[-1].data
        text = raw.decode("utf-8", errors="replace")
        if text.endswith("\r\n"):
            text = text[:-2]
        return text.split("\r\n") if text else []

    def start(self) -> FakeSMTPServer:
        """Start serving in a background thread and return ``self``."""
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        """Stop serving and close the listening socket."""
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> Self:
        """Start the server and return it."""
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Stop the server."""
        self.stop()


def make_tls_pair(server_name: str = "localhost") -> tuple[ssl.SSLContext, ssl.SSLContext]:
    """Create a test (server, client) TLS context pair trusting a fresh CA."""
    ca = trustme.CA()
    certificate = ca.issue_cert(server_name)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    certificate.configure_cert(server_context)
    client_context = ssl.create_default_context()
    ca.configure_trust(client_context)
    return server_context, client_context


@contextlib.contextmanager
def running_server(config: FakeSMTPConfig | None = None) -> Iterator[FakeSMTPServer]:
    """Run a fake server for the duration of the context."""
    server = FakeSMTPServer(config).start()
    try:
        yield server
    finally:
        server.stop()
