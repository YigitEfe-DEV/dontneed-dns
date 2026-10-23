"""End to end tests of the DNS server over real sockets."""

from __future__ import annotations

import socket
import struct
import threading
import time

import pytest

from dontneed_dns.core.dnsmessage import build_query, parse_message
from dontneed_dns.core.doh import DohClient, DohProvider
from dontneed_dns.core.resolver import SplitResolver
from dontneed_dns.dns.server import DnsServer
from tests.conftest import build_a_response

pytestmark = pytest.mark.integration


class FakeSystemResolver:
    """Answers every query with a fixed address."""

    def query(self, wire: bytes) -> bytes:
        return build_a_response(wire, address="198.51.100.5")

    def supports_type(self, qtype: int) -> bool:
        return True


@pytest.fixture
def server(doh_transport):
    """A DNS server on a free loopback port."""
    transport = doh_transport()
    provider = DohProvider("test", "192.0.2.1", "dns.test.example")
    resolver = SplitResolver(
        DohClient([provider], transport=transport),
        FakeSystemResolver(),
        split_domains=("discord.com",),
    )
    instance = DnsServer(
        resolver,
        host="127.0.0.1",
        port=0,
        host_v6=None,
        enable_tcp=True,
    )
    # Port 0 lets the operating system pick a free port.
    instance.bind()

    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.2)
    try:
        yield instance
    finally:
        instance.shutdown()
        thread.join(timeout=5)


def udp_query(port: int, payload: bytes, timeout: float = 5.0) -> bytes:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(payload, ("127.0.0.1", port))
        data, _ = sock.recvfrom(4096)
    return data


def tcp_query(port: int, payload: bytes, timeout: float = 5.0) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(struct.pack("!H", len(payload)) + payload)
        header = sock.recv(2)
        length = struct.unpack("!H", header)[0]
        buffer = bytearray()
        while len(buffer) < length:
            buffer.extend(sock.recv(length - len(buffer)))
    return bytes(buffer)


def test_a_udp_query_is_answered(server) -> None:
    response = udp_query(server.port, build_query("discord.com"))
    parsed = parse_message(response)
    assert parsed.header.rcode == 0
    assert parsed.header.ancount == 1
    assert parsed.question is not None
    assert parsed.question.name == "discord.com"


def test_a_tcp_query_is_answered(server) -> None:
    response = tcp_query(server.port, build_query("discord.com"))
    parsed = parse_message(response)
    assert parsed.header.rcode == 0
    assert parsed.header.ancount == 1


def test_the_answer_carries_the_identifier_of_the_request(server) -> None:
    query = build_query("discord.com", ident=0x4321)
    response = udp_query(server.port, query)
    assert parse_message(response).header.ident == 0x4321


def test_the_answer_is_marked_as_a_response(server) -> None:
    """The QR bit of the answer comes from the upstream, not from the request."""
    response = udp_query(server.port, build_query("discord.com"))
    parsed = parse_message(response)
    assert parsed.header.is_response is True
    assert parsed.header.rcode == 0


def test_a_non_split_domain_uses_the_system_resolver(server) -> None:
    response = udp_query(server.port, build_query("example.org"))
    parsed = parse_message(response)
    assert parsed.header.rcode == 0
    assert parsed.header.ancount == 1


def test_a_malformed_request_gets_a_formerr(server) -> None:
    response = udp_query(server.port, b"\x00\x01\x02\x03")
    parsed = parse_message(response)
    assert parsed.header.is_response is True
    assert parsed.header.rcode == 1


def test_a_request_without_a_question_gets_an_error(server) -> None:
    response = udp_query(server.port, struct.pack("!HHHHHH", 1, 0, 0, 0, 0, 0))
    assert parse_message(response).header.rcode == 1


def test_an_unsupported_opcode_gets_notimp(server) -> None:
    query = bytearray(build_query("discord.com"))
    query[2] = 0x08  # opcode 1 (inverse query)
    response = udp_query(server.port, bytes(query))
    assert parse_message(response).header.rcode == 4


def test_the_server_releases_its_ports_on_shutdown(server) -> None:
    port = server.port
    server.shutdown()
    time.sleep(0.3)
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=2)


def test_a_busy_port_is_reported_clearly(doh_transport) -> None:
    """The error message names the address that could not be opened."""
    transport = doh_transport()
    provider = DohProvider("test", "192.0.2.1", "dns.test.example")
    resolver = SplitResolver(
        DohClient([provider], transport=transport),
        FakeSystemResolver(),
        split_domains=("discord.com",),
    )

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        port = taken.getsockname()[1]

        instance = DnsServer(
            resolver,
            host="127.0.0.1",
            port=port,
            host_v6=None,
            enable_tcp=True,
        )
        with pytest.raises(OSError, match="already in use"):
            instance.bind()
        instance.shutdown()
