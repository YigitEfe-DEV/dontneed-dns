"""End to end tests of the proxy over real sockets."""

from __future__ import annotations

import socket
import struct
import threading
import time

import pytest

from dontneed_dns.core.config import ProxyConfig
from dontneed_dns.proxy import socks5
from dontneed_dns.proxy.server import ProxyServer

pytestmark = pytest.mark.integration


class EchoServer:
    """A TCP server that sends back everything it receives."""

    def __init__(self) -> None:
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(8)
        self.port = self.socket.getsockname()[1]
        self.received: list[bytes] = []
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while True:
            try:
                client, _address = self.socket.accept()
            except OSError:
                return
            with client:
                while True:
                    try:
                        data = client.recv(4096)
                    except OSError:
                        break
                    if not data:
                        break
                    self.received.append(data)
                    client.sendall(data)

    def close(self) -> None:
        try:
            self.socket.close()
        except OSError:
            pass


class StaticResolver:
    """Resolves every name to the loopback interface."""

    def __init__(self, port: int) -> None:
        self.port = port

    def resolve_addresses(self, name: str) -> list[str]:
        return ["127.0.0.1"]

    def resolve_wire(self, wire: bytes) -> None:
        return None


@pytest.fixture
def echo_server():
    server = EchoServer()
    try:
        yield server
    finally:
        server.close()


@pytest.fixture
def proxy(echo_server):
    config = ProxyConfig(
        listen_host="127.0.0.1",
        listen_port=0,
        resolve_via_doh=True,
        allow_system_dns_fallback=False,
    )
    server = ProxyServer(StaticResolver(echo_server.port), config)
    server.bind()
    # Port 0 was used for the bind, read the real port back.
    config.listen_port = server.bound[1]  # type: ignore[index]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.2)
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)


def socks5_connect(port: int, host: str, target_port: int) -> socket.socket:
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    sock.settimeout(5)
    socks5.client_greeting(sock)
    request = b"\x05\x01\x00\x03" + bytes([len(host)]) + host.encode() + struct.pack("!H", target_port)
    sock.sendall(request)
    reply = sock.recv(10)
    assert reply[1] == 0x00, f"the proxy refused the connection: {reply!r}"
    return sock


def test_a_socks5_connection_is_relayed(proxy, echo_server) -> None:
    sock = socks5_connect(proxy.bound[1], "test.local", echo_server.port)  # type: ignore[index]
    try:
        sock.sendall(b"hello proxy")
        assert sock.recv(1024) == b"hello proxy"
    finally:
        sock.close()


def test_an_http_connect_connection_is_relayed(proxy, echo_server) -> None:
    sock = socket.create_connection(("127.0.0.1", proxy.bound[1]), timeout=5)  # type: ignore[index]
    sock.settimeout(5)
    try:
        sock.sendall(f"CONNECT test.local:{echo_server.port} HTTP/1.1\r\nHost: test.local\r\n\r\n".encode())
        response = sock.recv(1024)
        assert response.startswith(b"HTTP/1.1 200")
        sock.sendall(b"hello http")
        assert sock.recv(1024) == b"hello http"
    finally:
        sock.close()


def test_the_proxy_refuses_a_plain_http_request(proxy) -> None:
    sock = socket.create_connection(("127.0.0.1", proxy.bound[1]), timeout=5)  # type: ignore[index]
    sock.settimeout(5)
    try:
        sock.sendall(b"GET / HTTP/1.1\r\n\r\n")
        response = sock.recv(1024)
        assert response.startswith(b"HTTP/1.1 405")
    finally:
        sock.close()


def test_the_proxy_refuses_a_non_connect_socks5_command(proxy, echo_server) -> None:
    sock = socket.create_connection(("127.0.0.1", proxy.bound[1]), timeout=5)  # type: ignore[index]
    sock.settimeout(5)
    try:
        socks5.client_greeting(sock)
        sock.sendall(b"\x05\x02\x00\x01" + socket.inet_aton("127.0.0.1") + struct.pack("!H", 80))
        reply = sock.recv(10)
        assert reply[1] != 0x00
    finally:
        sock.close()


def test_the_proxy_reports_an_unreachable_target(proxy) -> None:
    sock = socket.create_connection(("127.0.0.1", proxy.bound[1]), timeout=5)  # type: ignore[index]
    sock.settimeout(5)
    try:
        socks5.client_greeting(sock)
        # Port 1 is not listening on the loopback interface.
        sock.sendall(b"\x05\x01\x00\x01" + socket.inet_aton("127.0.0.1") + struct.pack("!H", 1))
        reply = sock.recv(10)
        assert reply[1] == socks5.REPLY_HOST_UNREACHABLE
    finally:
        sock.close()


def test_the_proxy_releases_the_port_on_shutdown(proxy) -> None:
    port = proxy.bound[1]  # type: ignore[index]
    proxy.shutdown()
    time.sleep(1.0)
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=2)


def test_a_client_hello_is_fragmented_when_the_feature_is_enabled(echo_server) -> None:
    config = ProxyConfig(
        listen_host="127.0.0.1",
        listen_port=0,
        resolve_via_doh=True,
        allow_system_dns_fallback=False,
        fragmentation_enabled=True,
        fragment_preamble_delay_ms=0,
        fragment_chunk_delay_ms=0,
    )
    server = ProxyServer(StaticResolver(echo_server.port), config)
    server.bind()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.2)
    try:
        sock = socks5_connect(server.bound[1], "test.local", echo_server.port)  # type: ignore[index]
        payload = b"\x16\x03\x03" + (100).to_bytes(2, "big") + b"\x01" + b"\x00" * 99
        sock.sendall(payload)
        received = b""
        deadline = time.monotonic() + 5
        while len(received) < len(payload) and time.monotonic() < deadline:
            chunk = sock.recv(4096)
            if not chunk:
                break
            received += chunk
        sock.close()
        assert received == payload
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_the_resolver_uses_doh_when_it_is_enabled(echo_server) -> None:
    """The proxy resolves a name over DNS-over-HTTPS when it is configured to."""

    class DohResolver:
        def __init__(self) -> None:
            self.queries: list[str] = []

        def resolve_addresses(self, name: str) -> list[str]:
            self.queries.append(name)
            return ["127.0.0.1"]

        def resolve_wire(self, wire: bytes) -> None:
            return None

    resolver = DohResolver()
    config = ProxyConfig(listen_host="127.0.0.1", listen_port=0, resolve_via_doh=True)
    server = ProxyServer(resolver, config)  # type: ignore[arg-type]
    server.bind()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.2)
    try:
        sock = socks5_connect(server.bound[1], "discord.com", echo_server.port)  # type: ignore[index]
        sock.close()
        assert resolver.queries == ["discord.com"]
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_a_doh_client_is_built_from_the_configuration() -> None:
    from dontneed_dns.core.config import Config
    from dontneed_dns.service_runner import build_resolver

    config = Config()
    resolver = build_resolver(config)
    assert resolver.doh.providers
    assert resolver.should_route_to_doh("discord.com") is True
    assert resolver.should_route_to_doh("example.org") is False
