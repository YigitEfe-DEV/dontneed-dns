"""Forwarding of non-routed DNS queries to the operating system resolver.

The split resolver only sends the configured Discord domains to
DNS-over-HTTPS.  Everything else must keep resolving exactly like it did
before, otherwise the project would break unrelated applications.

Two implementations exist:

``UdpSystemResolver``
    Reads ``/etc/resolv.conf`` and forwards the raw DNS message over UDP
    (with a TCP retry when the answer is truncated).  This is the default on
    Linux and supports every record type.

``GetAddrInfoResolver``
    Uses the operating system resolver through :func:`socket.getaddrinfo`.
    Windows cannot forward a raw DNS message without either a third party
    library or a private API, so the system fallback there is limited to
    A/AAAA records.  This limitation is documented in ``docs/windows.md``.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import struct
from abc import ABC, abstractmethod
from pathlib import Path

from dontneed_dns.core.constants import (
    DNS_TYPE_A,
    DNS_TYPE_AAAA,
    MAX_UDP_PAYLOAD,
    SOCKET_BUFFER_SIZE,
)
from dontneed_dns.core.dnsmessage import build_address_response, parse_message
from dontneed_dns.core.errors import DNSProtocolError

__all__ = [
    "GetAddrInfoResolver",
    "SystemResolver",
    "UdpSystemResolver",
    "create_system_resolver",
    "read_resolv_conf_nameservers",
]

RESOLV_CONF = "/etc/resolv.conf"


class SystemResolver(ABC):
    """Interface for the operating system resolver fallback."""

    @abstractmethod
    def query(self, wire: bytes) -> bytes | None:
        """Forward a raw DNS message and return the raw response.

        ``None`` is returned when the query cannot be handled (for example an
        unsupported record type); the caller then answers ``NOTIMP``.
        """

    def supports_type(self, qtype: int) -> bool:
        """Whether this resolver can answer questions of the given type."""
        return True


def read_resolv_conf_nameservers(path: str | os.PathLike[str] = RESOLV_CONF) -> list[str]:
    """Return the nameserver addresses configured in ``resolv.conf``."""
    servers: list[str] = []
    try:
        content = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return servers
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "nameserver":
            candidate = parts[1]
            try:
                ipaddress.ip_address(candidate)
            except ValueError:
                continue
            if candidate not in servers:
                servers.append(candidate)
    return servers


class UdpSystemResolver(SystemResolver):
    """Forward raw DNS messages to the nameservers from ``resolv.conf``."""

    def __init__(
        self,
        nameservers: list[str] | None = None,
        *,
        timeout: float = 3.0,
        resolv_conf: str | os.PathLike[str] = RESOLV_CONF,
    ) -> None:
        self.nameservers = (
            list(nameservers) if nameservers is not None else read_resolv_conf_nameservers(resolv_conf)
        )
        self.timeout = timeout

    def query(self, wire: bytes) -> bytes | None:
        if not self.nameservers:
            return None
        last_error: Exception | None = None
        for server in self.nameservers:
            try:
                response = self._query_udp(server, wire)
            except (OSError, DNSProtocolError) as exc:
                last_error = exc
                continue
            if response is None:
                continue
            if response[2] & 0x02:  # TC bit -> retry over TCP
                try:
                    tcp_response = self._query_tcp(server, wire)
                except OSError as exc:
                    last_error = exc
                    continue
                if tcp_response is not None:
                    return tcp_response
            return response
        if last_error is not None:
            raise last_error
        return None

    def _query_udp(self, server: str, wire: bytes) -> bytes | None:
        family = socket.AF_INET6 if ":" in server else socket.AF_INET
        with socket.socket(family, socket.SOCK_DGRAM) as sock:
            sock.settimeout(self.timeout)
            sock.sendto(wire, (server, 53))
            data, _ = sock.recvfrom(MAX_UDP_PAYLOAD)
        return data or None

    def _query_tcp(self, server: str, wire: bytes) -> bytes | None:
        family = socket.AF_INET6 if ":" in server else socket.AF_INET
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(self.timeout)
            sock.connect((server, 53))
            sock.sendall(struct.pack("!H", len(wire)) + wire)
            header = self._recv_exactly(sock, 2)
            length = struct.unpack("!H", header)[0]
            return self._recv_exactly(sock, length)

    @staticmethod
    def _recv_exactly(sock: socket.socket, count: int) -> bytes:
        buffer = bytearray()
        while len(buffer) < count:
            chunk = sock.recv(min(count - len(buffer), SOCKET_BUFFER_SIZE))
            if not chunk:
                raise OSError("connection closed while reading the DNS response")
            buffer.extend(chunk)
        return bytes(buffer)


class GetAddrInfoResolver(SystemResolver):
    """Resolve through the operating system resolver (``getaddrinfo``)."""

    def __init__(self, timeout: float = 3.0) -> None:
        self.timeout = timeout
        self._previous_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(self.timeout)

    def supports_type(self, qtype: int) -> bool:
        return qtype in (DNS_TYPE_A, DNS_TYPE_AAAA)

    def query(self, wire: bytes) -> bytes | None:
        parsed = parse_message(wire)
        question = parsed.question
        if question is None:
            return None
        if question.qtype == DNS_TYPE_A:
            family = socket.AF_INET
        elif question.qtype == DNS_TYPE_AAAA:
            family = socket.AF_INET6
        else:
            return None

        try:
            infos = socket.getaddrinfo(
                question.name,
                None,
                family,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                socket.AI_ADDRCONFIG,
            )
        except socket.gaierror:
            return build_address_response(wire, [])

        addresses: list[str] = []
        for info in infos:
            sockaddr = info[4]
            address = sockaddr[0]
            if address not in addresses:
                addresses.append(address)
        return build_address_response(wire, addresses)

    def close(self) -> None:
        """Restore the global socket timeout."""
        socket.setdefaulttimeout(self._previous_timeout)


def create_system_resolver(timeout: float = 3.0) -> SystemResolver:
    """Return the best available system resolver for the running platform."""
    servers = read_resolv_conf_nameservers()
    if servers:
        return UdpSystemResolver(servers, timeout=timeout)
    return GetAddrInfoResolver(timeout=timeout)
