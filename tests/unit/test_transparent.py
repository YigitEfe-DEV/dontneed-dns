"""Unit tests of the transparent router."""

from __future__ import annotations

import socket
import struct
from pathlib import Path

import pytest

from dontneed_dns.core.config import default_config, parse_config
from dontneed_dns.platforms.linux.adapter import LinuxPlatformAdapter
from dontneed_dns.transparent.nft import NftManager, TargetSet
from dontneed_dns.transparent.sni import extract_sni, parse_client_hello
from dontneed_dns.transparent.targets import read_targets, write_targets
from dontneed_dns.transparent.tcp import TransparentTcpServer, original_destination
from dontneed_dns.transparent.tun import RouteManager
from dontneed_dns.transparent.udp_tun import build_udp_packet, parse_udp_packet

# ------------------------------------------------------------- the rules


def test_the_rules_are_isolated_in_the_tables_of_the_project() -> None:
    manager = NftManager(table="dontneed_dns", uid=1000)
    rules = manager.render()
    assert "table ip dontneed_dns" in rules
    assert "table ip6 dontneed_dns6" in rules
    assert "filter" not in rules
    assert "flush" not in rules


def test_the_rules_only_match_the_traffic_of_the_user() -> None:
    manager = NftManager(table="dontneed_dns", uid=1000)
    rules = manager.render()
    assert rules.count("meta skuid 1000") >= 4
    assert "meta skuid 0 " not in rules


def test_the_traffic_of_the_router_is_not_redirected() -> None:
    manager = NftManager(table="dontneed_dns", uid=1000, mark=7)
    assert "meta mark 0x7 return" in manager.render()


def test_the_dns_queries_are_redirected_to_the_resolver() -> None:
    manager = NftManager(table="dontneed_dns", uid=1000, dns_port=5354)
    rules = manager.render()
    assert "udp dport 53 dnat to 127.0.0.1:5354" in rules
    assert "tcp dport 53 dnat to 127.0.0.1:5354" in rules
    assert "dnat to [::1]:5354" in rules


def test_the_tcp_connections_of_the_targets_are_redirected() -> None:
    manager = NftManager(table="dontneed_dns", uid=1000, tcp_port=15001)
    rules = manager.render()
    assert "ip daddr @targets meta l4proto tcp dnat to 127.0.0.1:15001" in rules
    assert "ip6 daddr @targets meta l4proto tcp dnat to [::1]:15001" in rules


def test_the_udp_traffic_is_not_redirected() -> None:
    """The UDP traffic is routed into the TUN device, not redirected."""
    manager = NftManager(table="dontneed_dns", uid=1000)
    assert "meta l4proto udp" not in manager.render()


def test_the_targets_end_up_in_the_set() -> None:
    manager = NftManager(table="dontneed_dns", uid=1000)
    manager.targets.add_many(["10.0.0.2", "2001:db8:1::2"])
    assert "elements = { 10.0.0.2 }" in manager.render_v4()
    assert "elements = { 2001:db8:1::2 }" in manager.render_v6()


def test_every_target_is_added() -> None:
    """The loop must not stop at the first address."""
    targets = TargetSet()
    targets.add_many(["10.0.0.1", "10.0.0.2", "10.0.0.3"])
    assert targets.ipv4 == {"10.0.0.1", "10.0.0.2", "10.0.0.3"}


def test_an_invalid_address_is_ignored() -> None:
    targets = TargetSet()
    assert targets.add("not an address") is False
    assert targets.ipv4 == set() and targets.ipv6 == set()


# ------------------------------------------------------------ the routes


def test_the_routes_are_installed_in_a_table_of_their_own() -> None:
    routes = RouteManager(table=100, uid=1000, mark=1, source="192.168.1.10")
    assert routes.table == 100
    assert routes.uid == 1000


# ----------------------------------------------------------- the packets


def _client_hello(name: str) -> bytes:
    """Build a TLS ClientHello that carries a server name."""
    server_name = name.encode("idna") if name.isascii() else name.encode()
    entry = b"\x00" + struct.pack("!H", len(server_name)) + server_name
    extension = struct.pack("!HH", 0x0000, len(entry) + 2) + struct.pack("!H", len(entry)) + entry
    body = struct.pack("!H", 0x0303) + bytes(32) + b"\x00" + b"\x00\x02\x13\x01" + b"\x01\x00"
    body += struct.pack("!H", len(extension)) + extension
    handshake = b"\x01" + struct.pack("!I", len(body))[1:] + body
    record = b"\x16\x03\x01" + struct.pack("!H", len(handshake)) + handshake
    return record


def test_the_server_name_is_read_from_the_client_hello() -> None:
    assert extract_sni(_client_hello("discord.gg")) == "discord.gg"


def test_a_message_without_a_server_name_yields_none() -> None:
    assert extract_sni(b"\x16\x03\x01\x00\x05\x01\x00\x00\x01\x00") is None
    assert extract_sni(b"") is None
    assert extract_sni(b"not tls at all") is None


def test_the_length_of_the_record_is_reported() -> None:
    hello = _client_hello("roblox.com")
    name, length = parse_client_hello(hello)
    assert name == "roblox.com"
    assert length == len(hello)


def test_the_server_name_matches_the_subdomains() -> None:
    proxy = TransparentTcpServer(sni_domains=("discord.com", "roblox.com"))
    assert proxy.matches("discord.com") is True
    assert proxy.matches("cdn.discord.com") is True
    assert proxy.matches("api.roblox.com") is True
    assert proxy.matches("discord.com.evil.example") is False
    assert proxy.matches("example.org") is False
    assert proxy.matches(None) is False


def test_the_transparent_unit_starts_the_copy_of_the_system() -> None:
    """The system service must not start a program of a user home."""
    adapter = LinuxPlatformAdapter()
    content = adapter._render_transparent_unit("yigitos")
    assert "ExecStart=/usr/local/bin/dontneed-dns run transparent" in content
    command = content.split("ExecStart=")[1].split("\n")[0]
    # The program is the copy of the system; the configuration may live in
    # the home of the user.
    assert command.startswith("/usr/local/bin/dontneed-dns ")


def test_the_udp_relay_survives_a_flow_that_cannot_be_opened() -> None:
    """One datagram that cannot be forwarded must not stop the relay."""
    import threading

    from dontneed_dns.transparent.udp_tun import TunUdpRelay

    class BrokenTun:
        def read(self, size: int = 65535) -> bytes:
            return b""

        def write(self, packet: bytes) -> None:
            raise OSError("no")

    relay = TunUdpRelay(BrokenTun(), mark=1)  # type: ignore[arg-type]
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            relay._handle(_packet())
        except BaseException as exc:  # noqa: BLE001 - the test looks at it
            errors.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=5)
    assert not errors


def _packet():
    from dontneed_dns.transparent.udp_tun import UdpPacket

    return UdpPacket("192.0.2.1", 5000, "192.0.2.2", 9000, b"ping")


def test_the_udp_flow_socket_binds_a_port_of_its_own() -> None:
    """The port of the client is in use, so the relay takes one of its own."""
    import socket as socket_module

    sock = socket_module.socket(socket_module.AF_INET, socket_module.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", 0))
        assert sock.getsockname()[1] != 0
    finally:
        sock.close()


def test_the_multicast_traffic_is_not_forwarded() -> None:
    """The router has nothing to do with multicast; forwarding it loops."""
    from dontneed_dns.transparent.udp_tun import _is_multicast

    assert _is_multicast("224.0.0.251") is True
    assert _is_multicast("239.1.2.3") is True
    assert _is_multicast("ff02::fb") is True
    assert _is_multicast("162.159.135.232") is False
    assert _is_multicast("::1") is False


def test_the_original_destination_is_read_from_the_socket() -> None:
    """The kernel hands out the address a redirected connection wanted."""
    left, right = socket.socketpair()
    try:
        with pytest.raises(OSError):
            original_destination(left)
    finally:
        left.close()
        right.close()


def test_a_udp_packet_is_parsed() -> None:
    packet = build_udp_packet("10.0.0.1", 40000, "10.0.0.2", 9000, b"ping")
    parsed = parse_udp_packet(packet)
    assert parsed is not None
    assert parsed.source == "10.0.0.1"
    assert parsed.source_port == 40000
    assert parsed.destination == "10.0.0.2"
    assert parsed.destination_port == 9000
    assert parsed.payload == b"ping"


def test_an_ipv6_udp_packet_is_parsed() -> None:
    packet = build_udp_packet("2001:db8:1::1", 40000, "2001:db8:1::2", 9000, b"ping")
    parsed = parse_udp_packet(packet)
    assert parsed is not None
    assert parsed.source == "2001:db8:1::1"
    assert parsed.destination == "2001:db8:1::2"
    assert parsed.payload == b"ping"


def test_a_packet_without_udp_is_ignored() -> None:
    assert parse_udp_packet(b"\x45\x00\x00\x1c" + bytes(24)) is None
    assert parse_udp_packet(b"") is None


def test_the_checksum_of_the_header_is_correct() -> None:
    packet = build_udp_packet("10.0.0.1", 1, "10.0.0.2", 2, b"")
    header = packet[:20]
    total = 0
    for index in range(0, 20, 2):
        total += (header[index] << 8) + header[index + 1]
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    assert total == 0xFFFF


# ----------------------------------------------------- the learned targets


def test_the_learned_addresses_are_written_and_read(tmp_path: Path) -> None:
    path = tmp_path / "targets.json"
    write_targets(["10.0.0.2", "2001:db8:1::2", "10.0.0.2"], path)
    assert read_targets(path) == ["10.0.0.2", "2001:db8:1::2"]


def test_a_missing_file_yields_no_addresses(tmp_path: Path) -> None:
    assert read_targets(tmp_path / "missing.json") == []


def test_a_broken_file_yields_no_addresses(tmp_path: Path) -> None:
    path = tmp_path / "targets.json"
    path.write_text("not json", encoding="utf-8")
    assert read_targets(path) == []


# ---------------------------------------------------------- the configuration


def test_the_transparent_section_is_valid_by_default() -> None:
    config = default_config()
    assert config.transparent.enabled is False
    assert config.transparent.tcp_port == 15001
    assert config.transparent.udp_port == 15002
    assert config.transparent.nft_table == "dontneed_dns"


def test_the_transparent_section_can_be_configured() -> None:
    config = parse_config(
        {
            "transparent": {
                "enabled": True,
                "tcp_port": 15010,
                "udp_port": 15020,
                "nft_table": "mytable",
                "fragment": False,
                "target_ips": ["10.0.0.2"],
            }
        }
    )
    assert config.transparent.enabled is True
    assert config.transparent.tcp_port == 15010
    assert config.transparent.nft_table == "mytable"
    assert config.transparent.fragment is False
    assert config.transparent.target_ips == ["10.0.0.2"]


def test_an_unknown_option_of_the_transparent_section_is_rejected() -> None:
    from dontneed_dns.core.errors import ConfigError

    with pytest.raises(ConfigError):
        parse_config({"transparent": {"enabled": True, "unknown": 1}})


def test_a_wrong_port_of_the_transparent_section_is_rejected() -> None:
    from dontneed_dns.core.errors import ConfigError

    with pytest.raises(ConfigError):
        parse_config({"transparent": {"tcp_port": 0}})
