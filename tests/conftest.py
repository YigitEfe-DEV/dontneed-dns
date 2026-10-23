"""Shared test fixtures."""

from __future__ import annotations

import socket
import struct
from pathlib import Path

import pytest

from dontneed_dns.core.config import Config, DnsConfig, ProxyConfig, config_to_toml
from dontneed_dns.core.dnsmessage import encode_name
from dontneed_dns.core.doh import DohProvider


@pytest.fixture
def provider() -> DohProvider:
    """A provider that is never contacted."""
    return DohProvider("test", "192.0.2.1", "dns.test.example")


@pytest.fixture
def config() -> Config:
    """A configuration with the default values."""
    return Config()


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    """A configuration file on disk."""
    path = tmp_path / "config.toml"
    path.write_text(config_to_toml(Config()), encoding="utf-8")
    return path


def build_a_response(query: bytes, address: str = "203.0.113.10", ttl: int = 60) -> bytes:
    """Build a DNS response with one A record for the query."""
    ident = struct.unpack("!H", query[:2])[0]
    # The question is echoed back unchanged.
    offset = 12
    while offset < len(query):
        length = query[offset]
        if length == 0:
            offset += 1
            break
        offset += length + 1
    question = query[12 : offset + 4]
    packed = socket.inet_pton(socket.AF_INET, address)
    header = struct.pack("!HHHHHH", ident, 0x8180, 1, 1, 0, 0)
    answer = encode_name("discord.com") + struct.pack("!HHIH", 1, 1, ttl, len(packed)) + packed
    return header + question + answer


@pytest.fixture
def doh_transport():
    """Return a factory for a fake DNS-over-HTTPS transport."""

    def factory(response: bytes | None = None, error: Exception | None = None):
        calls: list[bytes] = []

        def transport(provider, body, timeout, context):
            calls.append(body)
            if error is not None:
                raise error
            if response is None:
                return build_a_response(body)
            return response

        transport.calls = calls  # type: ignore[attr-defined]
        return transport

    return factory


@pytest.fixture
def dns_config() -> DnsConfig:
    """A DNS configuration that does not touch the real network."""
    return DnsConfig(listen_host="127.0.0.1", listen_port=15354, listen_host_v6=None)


@pytest.fixture
def proxy_config() -> ProxyConfig:
    """A proxy configuration that does not touch the real network."""
    return ProxyConfig(listen_host="127.0.0.1", listen_port=19080)
