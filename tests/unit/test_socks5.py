"""Tests of the SOCKS5 protocol implementation."""

from __future__ import annotations

import socket
import struct
import threading

import pytest

from dontneed_dns.proxy import socks5


class FakeSocket:
    """A socket that replays a scripted conversation."""

    def __init__(self, incoming: bytes) -> None:
        self.incoming = bytearray(incoming)
        self.sent = bytearray()
        self._closed = False

    def recv(self, count: int, flags: int = 0) -> bytes:
        if not self.incoming:
            return b""
        chunk = bytes(self.incoming[:count])
        del self.incoming[:count]
        return chunk

    def sendall(self, data: bytes) -> None:
        self.sent.extend(data)

    def close(self) -> None:
        self._closed = True

    def settimeout(self, _timeout: float) -> None:
        pass


def test_negotiation_accepts_the_no_authentication_method() -> None:
    sock = FakeSocket(b"\x05\x01\x00")
    socks5.negotiate(sock)  # type: ignore[arg-type]
    assert bytes(sock.sent) == b"\x05\x00"


def test_negotiation_rejects_a_missing_no_authentication_method() -> None:
    sock = FakeSocket(b"\x05\x01\x02")
    with pytest.raises(socks5.Socks5Error):
        socks5.negotiate(sock)  # type: ignore[arg-type]
    assert bytes(sock.sent) == b"\x05\xff"


def test_negotiation_rejects_a_wrong_version() -> None:
    sock = FakeSocket(b"\x04\x01\x00")
    with pytest.raises(socks5.Socks5Error):
        socks5.negotiate(sock)  # type: ignore[arg-type]


def test_a_request_split_over_two_segments_is_read_correctly() -> None:
    request = b"\x05\x01\x00\x03" + bytes([11]) + b"discord.com" + struct.pack("!H", 443)
    # The first recv only returns the first four bytes.
    sock = FakeSocket(request)
    parsed = socks5.read_request(sock)  # type: ignore[arg-type]
    assert parsed.host == "discord.com"
    assert parsed.port == 443
    assert parsed.atyp == socks5.ATYP_DOMAIN
    assert parsed.is_domain is True


def test_an_ipv4_target_is_read() -> None:
    request = b"\x05\x01\x00\x01" + socket.inet_aton("203.0.113.9") + struct.pack("!H", 8080)
    sock = FakeSocket(request)
    parsed = socks5.read_request(sock)  # type: ignore[arg-type]
    assert parsed.host == "203.0.113.9"
    assert parsed.port == 8080
    assert parsed.is_domain is False


def test_an_ipv6_target_is_read() -> None:
    packed = socket.inet_pton(socket.AF_INET6, "2001:db8::1")
    request = b"\x05\x01\x00\x04" + packed + struct.pack("!H", 443)
    sock = FakeSocket(request)
    parsed = socks5.read_request(sock)  # type: ignore[arg-type]
    assert parsed.host == "2001:db8::1"


def test_an_unsupported_address_type_is_rejected() -> None:
    sock = FakeSocket(b"\x05\x01\x00\x09")
    with pytest.raises(socks5.Socks5Error):
        socks5.read_request(sock)  # type: ignore[arg-type]


def test_a_bind_command_is_rejected() -> None:
    request = b"\x05\x02\x00\x01" + socket.inet_aton("203.0.113.9") + struct.pack("!H", 8080)
    sock = FakeSocket(request)
    with pytest.raises(socks5.Socks5Error):
        socks5.read_request(sock)  # type: ignore[arg-type]


def test_an_empty_domain_is_rejected() -> None:
    sock = FakeSocket(b"\x05\x01\x00\x03\x00")
    with pytest.raises(socks5.Socks5Error):
        socks5.read_request(sock)  # type: ignore[arg-type]


def test_a_closed_connection_is_reported() -> None:
    sock = FakeSocket(b"")
    with pytest.raises(socks5.Socks5Error):
        socks5.recv_exact(sock, 4)  # type: ignore[arg-type]


def test_the_success_reply_has_the_expected_layout() -> None:
    sock = FakeSocket(b"")
    socks5.send_reply(sock, socks5.REPLY_SUCCEEDED, "127.0.0.1", 10808)  # type: ignore[arg-type]
    sent = bytes(sock.sent)
    assert sent[0] == 0x05
    assert sent[1] == 0x00
    assert sent[3] == socks5.ATYP_IPV4
    assert struct.unpack("!H", sent[8:10])[0] == 10808


def test_a_real_socket_round_trip() -> None:
    """The negotiation and the request work over a real socket pair."""
    server_sock, client_sock = socket.socketpair()
    request = b"\x05\x01\x00\x03" + bytes([7]) + b"discord" + struct.pack("!H", 443)
    # "discord" is 7 bytes, the request continues with ".com".
    request = b"\x05\x01\x00\x03" + bytes([11]) + b"discord.com" + struct.pack("!H", 443)

    def client() -> None:
        client_sock.sendall(b"\x05\x01\x00")
        assert client_sock.recv(2) == b"\x05\x00"
        client_sock.sendall(request)
        client_sock.close()

    thread = threading.Thread(target=client)
    thread.start()

    socks5.negotiate(server_sock)
    parsed = socks5.read_request(server_sock)
    server_sock.close()
    thread.join(timeout=5)

    assert parsed.host == "discord.com"
    assert parsed.port == 443
