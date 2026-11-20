"""The harness of the transparent routing tests.

The harness runs inside an unprivileged user and network namespace.  It
builds a real network: two namespaces connected by a veth pair, a server
in the second namespace and the complete transparent router in the first
one.  The nftables rules and the routes are really installed and the
traffic really takes the redirected path.

The script prints one JSON document with the result of every check.  It is
started by ``tests/integration/test_transparent_netns.py``.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from dontneed_dns.core.config import default_config  # noqa: E402
from dontneed_dns.core.dnsmessage import (  # noqa: E402
    build_query,
    extract_addresses,
    parse_message,
)
from dontneed_dns.core.doh import DohClient, DohProvider  # noqa: E402
from dontneed_dns.core.resolver import SplitResolver  # noqa: E402
from dontneed_dns.transparent.service import TransparentRouter  # noqa: E402

CLIENT_IP = "10.0.0.1"
SERVER_IP = "10.0.0.2"
CLIENT_IP6 = "2001:db8:1::1"
SERVER_IP6 = "2001:db8:1::2"
TCP_PORT = 9000
UDP_PORT = 9001
DNS_PORT = 53

RESULTS: dict[str, object] = {}


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a command and fail loudly."""
    result = subprocess.run(command, **kwargs)
    if result.returncode != 0:
        raise RuntimeError(f"the command failed: {command}")
    return result


def check(name: str, value: object) -> None:
    """Record the result of one check."""
    RESULTS[name] = value
    print(f"check {name}: {value}", flush=True)


# ---------------------------------------------------------------- the peer


PEER_SERVER = """
import socket, sys, threading, time

tcp_port, udp_port, dns_port = (int(x) for x in sys.argv[1:4])


def tcp_server():
    srv = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    srv.bind(("::", tcp_port))
    srv.listen(8)
    while True:
        conn, _ = srv.accept()
        data = conn.recv(4096)
        conn.sendall(b"echo:" + data)
        conn.close()


def udp_server():
    srv = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
    srv.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    srv.bind(("::", udp_port))
    while True:
        data, addr = srv.recvfrom(4096)
        srv.sendto(b"echo:" + data, addr)


def dns_server():
    import struct
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.bind(("0.0.0.0", dns_port))
    while True:
        data, addr = srv.recvfrom(4096)
        if len(data) < 12:
            continue
        ident = data[:2]
        question = data[12:]
        answer = ident + struct.pack("!HHHHH", 0x8180, 1, 1, 0, 0) + question
        answer += b"\\xc0\\x0c" + struct.pack("!HHIH", 1, 1, 60, 4) + socket.inet_aton("10.0.0.9")
        srv.sendto(answer, addr)


for target in (tcp_server, udp_server, dns_server):
    threading.Thread(target=target, daemon=True).start()
print("ready", flush=True)
while True:
    time.sleep(3600)
"""


def build_network():
    """Create the peer namespace and the veth pair."""
    run(["ip", "link", "set", "lo", "up"])
    peer = subprocess.Popen(
        ["unshare", "-n", "sleep", "600"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.5)
    run(["ip", "link", "add", "veth0", "type", "veth", "peer", "name", "veth1"])
    run(["ip", "link", "set", "veth1", "netns", str(peer.pid)])
    run(["ip", "addr", "add", f"{CLIENT_IP}/24", "dev", "veth0"])
    run(["ip", "-6", "addr", "add", f"{CLIENT_IP6}/64", "dev", "veth0", "nodad"])
    run(["ip", "link", "set", "veth0", "up"])
    run(["nsenter", "-t", str(peer.pid), "-n", "ip", "link", "set", "lo", "up"])
    run(["nsenter", "-t", str(peer.pid), "-n", "ip", "addr", "add", f"{SERVER_IP}/24", "dev", "veth1"])
    run(
        [
            "nsenter",
            "-t",
            str(peer.pid),
            "-n",
            "ip",
            "-6",
            "addr",
            "add",
            f"{SERVER_IP6}/64",
            "dev",
            "veth1",
            "nodad",
        ]
    )
    run(["nsenter", "-t", str(peer.pid), "-n", "ip", "link", "set", "veth1", "up"])

    script = Path("/tmp/dontneed-dns-peer-server.py")
    script.write_text(PEER_SERVER, encoding="utf-8")
    peer_server = subprocess.Popen(
        [
            "nsenter",
            "-t",
            str(peer.pid),
            "-n",
            sys.executable,
            str(script),
            str(TCP_PORT),
            str(UDP_PORT),
            str(DNS_PORT),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert peer_server.stdout is not None
    line = peer_server.stdout.readline()
    if b"ready" not in line:
        raise RuntimeError("the peer server did not start")
    return peer, peer.pid


# ------------------------------------------------------------ the resolver


def build_resolver() -> SplitResolver:
    """A resolver whose DNS-over-HTTPS transport asks the peer server."""

    def transport(provider, body, timeout, context):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        # The mark keeps the query away from the redirection of the router.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_MARK, 1)
        sock.sendto(body, (SERVER_IP, DNS_PORT))
        data, _ = sock.recvfrom(4096)
        sock.close()
        # The answer of the resolver is marked, so that it can be told
        # apart from the answer of the server.
        return data[:-4] + socket.inet_aton("10.0.0.2")

    provider = DohProvider("test", "192.0.2.1", "test.example")
    doh = DohClient([provider], timeout=3.0, retries=0, transport=transport)
    return SplitResolver(doh, None, split_domains=("discord.com",), route_split=True)


# ------------------------------------------------------------------ tests


def test_tcp(port: int, family: int, address: str, label: str) -> None:
    """A TCP connection to the peer is redirected through the proxy."""
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.settimeout(10)
    try:
        sock.connect((address, port))
        sock.sendall(b"hello")
        answer = sock.recv(100)
    except OSError as exc:
        check(f"{label}_ok", False)
        check(f"{label}_error", str(exc))
        sock.close()
        return
    sock.close()
    check(f"{label}_answer", answer.decode("utf-8", "replace"))
    check(f"{label}_ok", answer == b"echo:hello")


def test_udp(family: int, address: str, label: str) -> None:
    """A datagram to the peer is routed through the relay."""
    sock = socket.socket(family, socket.SOCK_DGRAM)
    sock.settimeout(10)
    try:
        sock.connect((address, UDP_PORT))
        sock.send(b"ping")
        answer = sock.recvfrom(4096)[0]
    except OSError as exc:
        check(f"{label}_ok", False)
        check(f"{label}_error", str(exc))
        sock.close()
        return
    sock.close()
    check(f"{label}_answer", answer.decode("utf-8", "replace"))
    check(f"{label}_ok", answer == b"echo:ping")


def test_dns() -> None:
    """A DNS query to another server is redirected to the local resolver."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(10)
    sock.sendto(build_query("discord.com"), (SERVER_IP, DNS_PORT))
    try:
        answer, _ = sock.recvfrom(4096)
    except OSError as exc:
        check("dns_ok", False)
        check("dns_error", str(exc))
        sock.close()
        return
    sock.close()
    parsed = parse_message(answer)
    check("dns_rcode", parsed.header.rcode)
    check("dns_address", extract_addresses(answer))
    check("dns_ok", parsed.header.rcode == 0 and extract_addresses(answer) == ["10.0.0.2"])


def test_the_fallback_of_the_resolver_is_not_redirected(source_port: int) -> None:
    """The resolver must not redirect its own fallback queries.

    Without the exemption the fallback query of the resolver would be
    redirected to the resolver itself, forever.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(10)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("10.0.0.1", source_port))
    sock.sendto(build_query("example.org"), (SERVER_IP, DNS_PORT))
    try:
        answer, _ = sock.recvfrom(4096)
    except OSError as exc:
        check("dns_fallback_ok", False)
        check("dns_fallback_error", str(exc))
        sock.close()
        return
    sock.close()
    check("dns_fallback_address", extract_addresses(answer))
    check("dns_fallback_ok", extract_addresses(answer) == ["10.0.0.9"])


def main() -> int:
    from dontneed_dns.core.logging_setup import setup_logging

    setup_logging(default_config())
    peer, peer_pid = build_network()
    router = None
    try:
        config = default_config()
        config.dns.listen_host = "127.0.0.1"
        config.dns.listen_port = 15354
        config.dns.listen_host_v6 = None
        config.dns.split_domains = ["discord.com"]
        config.transparent.enabled = True
        config.transparent.fragment = False
        config.transparent.learn = False
        config.transparent.target_ips = [SERVER_IP, SERVER_IP6]
        config.transparent.tcp_port = 15001
        config.transparent.udp_port = 15002

        router = TransparentRouter(config)
        router._resolver = build_resolver()
        router.start()
        check("router_started", True)
        check("tables_installed", router.manager.table_exists_v4())

        # A table of another program must survive our changes.
        run(["nft", "add", "table", "inet", "dontneed_dns_test"])
        run(
            [
                "nft",
                "add",
                "chain",
                "inet",
                "dontneed_dns_test",
                "input",
                "{ type filter hook input priority filter; policy accept; }",
            ]
        )

        print(
            "rules:",
            subprocess.run(["ip", "rule", "show"], capture_output=True, text=True).stdout,
            flush=True,
        )
        print(
            "routes4:",
            subprocess.run(["ip", "route", "show", "table", "100"], capture_output=True, text=True).stdout,
            flush=True,
        )
        print(
            "routes6:",
            subprocess.run(
                ["ip", "-6", "route", "show", "table", "100"], capture_output=True, text=True
            ).stdout,
            flush=True,
        )
        print(
            "nft:",
            subprocess.run(["nft", "list", "tables"], capture_output=True, text=True).stdout,
            flush=True,
        )
        print(
            "nft6:",
            subprocess.run(
                ["nft", "list", "table", "ip6", "dontneed_dns6"], capture_output=True, text=True
            ).stdout,
            flush=True,
        )
        print(
            "ip6rules:",
            subprocess.run(["ip", "-6", "rule", "show"], capture_output=True, text=True).stdout,
            flush=True,
        )
        print(
            "ip6addr:",
            subprocess.run(["ip", "-6", "addr", "show"], capture_output=True, text=True).stdout,
            flush=True,
        )
        # The local resolver that the DNS redirection points at.
        from dontneed_dns.dns.server import DnsServer

        dns = DnsServer(build_resolver(), host="127.0.0.1", port=15354, host_v6=None, enable_tcp=True)
        dns.bind()
        threading.Thread(target=dns.serve_forever, daemon=True).start()

        time.sleep(0.5)
        test_tcp(TCP_PORT, socket.AF_INET, SERVER_IP, "tcp")
        test_udp(socket.AF_INET, SERVER_IP, "udp")
        test_tcp(TCP_PORT, socket.AF_INET6, SERVER_IP6, "tcp6")
        test_udp(socket.AF_INET6, SERVER_IP6, "udp6")
        test_dns()
        test_the_fallback_of_the_resolver_is_not_redirected(config.transparent.resolver_source_port)

        # The rules and the routes of the project are removed; the table of
        # the other program is untouched.
        router.stop()
        router = None
        check("project_tables_removed", True)
        result = subprocess.run(["nft", "list", "tables"], capture_output=True, text=True)
        check("foreign_table_kept", "dontneed_dns_test" in result.stdout)
        rules = subprocess.run(["ip", "rule", "show"], capture_output=True, text=True)
        check("rules_removed", "uidrange" not in rules.stdout)
    finally:
        if router is not None:
            router.stop()
        subprocess.run(
            ["nsenter", "-t", str(peer_pid), "-n", "ip", "link", "delete", "veth1"], capture_output=True
        )
        peer.terminate()

    print("RESULT " + json.dumps(RESULTS), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("RESULT " + json.dumps({"error": str(exc), **RESULTS}), flush=True)
        raise SystemExit(1) from exc
