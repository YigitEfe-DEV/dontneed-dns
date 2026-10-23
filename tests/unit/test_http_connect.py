"""Tests of the HTTP CONNECT implementation."""

from __future__ import annotations

import socket
import threading

import pytest

from dontneed_dns.proxy import http_connect


class FakeSocket:
    """A socket that replays a scripted conversation."""

    def __init__(self, incoming: bytes) -> None:
        self.incoming = bytearray(incoming)
        self.sent = bytearray()

    def recv(self, count: int, flags: int = 0) -> bytes:
        if not self.incoming:
            return b""
        chunk = bytes(self.incoming[:count])
        del self.incoming[:count]
        return chunk

    def sendall(self, data: bytes) -> None:
        self.sent.extend(data)

    def close(self) -> None:
        pass

    def settimeout(self, _timeout: float) -> None:
        pass


def test_a_connect_request_is_parsed() -> None:
    sock = FakeSocket(b"CONNECT discord.com:443 HTTP/1.1\r\nHost: discord.com\r\n\r\n")
    target = http_connect.read_connect_request(sock)  # type: ignore[arg-type]
    assert target.host == "discord.com"
    assert target.port == 443


def test_a_connect_request_without_a_port_defaults_to_443() -> None:
    sock = FakeSocket(b"CONNECT discord.com HTTP/1.1\r\n\r\n")
    target = http_connect.read_connect_request(sock)  # type: ignore[arg-type]
    assert target.port == 443


def test_an_ipv6_target_is_parsed() -> None:
    sock = FakeSocket(b"CONNECT [2001:db8::1]:8443 HTTP/1.1\r\n\r\n")
    target = http_connect.read_connect_request(sock)  # type: ignore[arg-type]
    assert target.host == "2001:db8::1"
    assert target.port == 8443


def test_a_plain_get_request_is_refused() -> None:
    sock = FakeSocket(b"GET / HTTP/1.1\r\n\r\n")
    with pytest.raises(http_connect.HttpConnectError, match="only CONNECT"):
        http_connect.read_connect_request(sock)  # type: ignore[arg-type]


def test_a_malformed_request_line_is_refused() -> None:
    sock = FakeSocket(b"CONNECT\r\n\r\n")
    with pytest.raises(http_connect.HttpConnectError):
        http_connect.read_connect_request(sock)  # type: ignore[arg-type]


def test_an_invalid_port_is_refused() -> None:
    sock = FakeSocket(b"CONNECT discord.com:abc HTTP/1.1\r\n\r\n")
    with pytest.raises(http_connect.HttpConnectError):
        http_connect.read_connect_request(sock)  # type: ignore[arg-type]


def test_an_incomplete_request_is_refused() -> None:
    sock = FakeSocket(b"CONNECT disc")
    with pytest.raises(http_connect.HttpConnectError):
        http_connect.read_connect_request(sock)  # type: ignore[arg-type]


def test_the_established_response_has_the_expected_layout() -> None:
    sock = FakeSocket(b"")
    http_connect.send_established(sock)  # type: ignore[arg-type]
    assert bytes(sock.sent).startswith(b"HTTP/1.1 200 Connection Established")


def test_the_error_response_carries_a_body() -> None:
    sock = FakeSocket(b"")
    http_connect.send_error(sock, 502, "Bad Gateway")  # type: ignore[arg-type]
    sent = bytes(sock.sent)
    assert sent.startswith(b"HTTP/1.1 502 Bad Gateway")
    assert b"Content-Length:" in sent


def test_a_real_socket_round_trip() -> None:
    server_sock, client_sock = socket.socketpair()

    def client() -> None:
        client_sock.sendall(b"CONNECT discord.com:443 HTTP/1.1\r\n\r\n")
        client_sock.recv(128)
        client_sock.close()

    thread = threading.Thread(target=client)
    thread.start()

    target = http_connect.read_connect_request(server_sock)
    http_connect.send_established(server_sock)
    server_sock.close()
    thread.join(timeout=5)

    assert target.host == "discord.com"
    assert target.port == 443
