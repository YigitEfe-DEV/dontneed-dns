"""Health checks shared by the CLI and the TUI.

The checks are read only: they open a loopback connection, send a DNS query
or a SOCKS5 greeting and report what happened.  Nothing is modified.
"""

from __future__ import annotations

import socket
import sys
from dataclasses import dataclass

from dontneed_dns.core.config import Config
from dontneed_dns.core.dnsmessage import build_query, parse_message
from dontneed_dns.core.logging_setup import get_logger
from dontneed_dns.core.upstream import read_resolv_conf_nameservers

__all__ = ["HealthCheck", "Severity", "run_health_checks"]

_LOGGER = get_logger("health")


class Severity:
    """How serious a failed check is."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class HealthCheck:
    """The result of a single check."""

    name: str
    ok: bool
    detail: str = ""
    severity: str = Severity.ERROR

    @property
    def status(self) -> str:
        """A short status word."""
        if self.ok:
            return "ok"
        return self.severity


def _tcp_probe(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _dns_probe(host: str, port: int, name: str, timeout: float = 3.0) -> tuple[bool, str]:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    query = build_query(name)
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            sock.sendto(query, (host, port))
            data, _ = sock.recvfrom(4096)
    except OSError as exc:
        return False, str(exc)
    try:
        parsed = parse_message(data)
    except Exception as exc:  # noqa: BLE001
        return False, f"the answer could not be parsed: {exc}"
    if parsed.header.rcode != 0:
        return False, f"the server answered with rcode {parsed.header.rcode}"
    if parsed.header.ancount == 0:
        return True, "the server answered but the name has no record"
    return True, f"{parsed.header.ancount} answer(s)"


def _socks5_probe(host: str, port: int, timeout: float = 2.0) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(b"\x05\x01\x00")
            reply = sock.recv(2)
    except OSError as exc:
        return False, str(exc)
    if reply == b"\x05\x00":
        return True, "the SOCKS5 greeting was accepted"
    return False, f"unexpected greeting answer: {reply!r}"


def run_health_checks(config: Config, *, timeout: float = 3.0) -> list[HealthCheck]:
    """Run every health check and return the results."""
    checks: list[HealthCheck] = []

    checks.append(
        HealthCheck(
            "Configuration",
            True,
            "the configuration file is valid",
            Severity.ERROR,
        )
    )

    dns_host = config.dns.listen_host
    dns_port = config.dns.listen_port
    listening = _tcp_probe(dns_host, dns_port, timeout)
    checks.append(
        HealthCheck(
            "DNS listener",
            listening,
            f"{dns_host}:{dns_port} is reachable"
            if listening
            else f"nothing is listening on {dns_host}:{dns_port}",
            Severity.ERROR,
        )
    )

    if listening:
        probe_domain = config.dns.split_domains[0] if config.dns.split_domains else "discord.com"
        ok, detail = _dns_probe(dns_host, dns_port, probe_domain, timeout)
        checks.append(
            HealthCheck(
                "DNS query",
                ok,
                f"{probe_domain}: {detail}",
                Severity.WARNING,
            )
        )
    else:
        checks.append(
            HealthCheck(
                "DNS query",
                False,
                "the DNS listener is not reachable",
                Severity.WARNING,
            )
        )

    proxy_host = config.proxy.listen_host
    proxy_port = config.proxy.listen_port
    proxy_listening = _tcp_probe(proxy_host, proxy_port, timeout)
    checks.append(
        HealthCheck(
            "Proxy listener",
            proxy_listening,
            f"{proxy_host}:{proxy_port} is reachable"
            if proxy_listening
            else f"nothing is listening on {proxy_host}:{proxy_port}",
            Severity.ERROR,
        )
    )

    if proxy_listening:
        ok, detail = _socks5_probe(proxy_host, proxy_port, timeout)
        checks.append(HealthCheck("SOCKS5 handshake", ok, detail, Severity.WARNING))
    else:
        checks.append(
            HealthCheck(
                "SOCKS5 handshake",
                False,
                "the proxy listener is not reachable",
                Severity.WARNING,
            )
        )

    if sys.platform.startswith("win"):
        # The Windows resolver cannot be read from a file.
        checks.append(
            HealthCheck(
                "System resolver",
                True,
                "the Windows resolver is used",
                Severity.WARNING,
            )
        )
    else:
        nameservers = read_resolv_conf_nameservers()
        checks.append(
            HealthCheck(
                "System resolver",
                bool(nameservers),
                f"nameservers: {', '.join(nameservers)}"
                if nameservers
                else "no nameserver found in /etc/resolv.conf",
                Severity.WARNING,
            )
        )

    fragment_state = "enabled" if config.proxy.fragmentation_enabled else "disabled"
    checks.append(
        HealthCheck(
            "TLS fragmentation",
            True,
            f"experimental feature is {fragment_state}",
            Severity.INFO,
        )
    )

    return checks
