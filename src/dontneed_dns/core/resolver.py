"""The split resolver.

Queries for the configured domains are answered over DNS-over-HTTPS, every
other query is forwarded to the operating system resolver.  This keeps the
behaviour of unrelated applications unchanged while removing the plain text
DNS lookup for the domains that matter.

The resolver is used by both the DNS server and the proxy, so that a domain
resolved by the proxy never leaks through the system resolver when DNS-over-
HTTPS is available.
"""

from __future__ import annotations

from dataclasses import dataclass

from dontneed_dns.core.dnsmessage import build_query, extract_addresses, matches_domain, parse_message
from dontneed_dns.core.doh import DohClient
from dontneed_dns.core.errors import DNSProtocolError
from dontneed_dns.core.upstream import SystemResolver

__all__ = ["ResolutionResult", "SplitResolver"]


@dataclass
class ResolutionResult:
    """The outcome of a resolution."""

    wire: bytes | None
    source: str = "none"
    error: str = ""

    @property
    def ok(self) -> bool:
        """``True`` when a response was produced."""
        return self.wire is not None


class SplitResolver:
    """Route DNS queries to DNS-over-HTTPS or to the system resolver."""

    def __init__(
        self,
        doh_client: DohClient,
        system_resolver: SystemResolver | None = None,
        *,
        split_domains: tuple[str, ...] | list[str] = (),
        route_split: bool = True,
        system_fallback: bool = True,
    ) -> None:
        self.doh = doh_client
        self.system = system_resolver
        self.split_domains = tuple(split_domains)
        self.route_split = route_split
        self.system_fallback = system_fallback

    def should_route_to_doh(self, name: str) -> bool:
        """``True`` when ``name`` must be resolved over DNS-over-HTTPS."""
        if not self.route_split:
            return False
        return matches_domain(name, self.split_domains)

    def resolve_wire(self, wire: bytes) -> ResolutionResult:
        """Resolve a raw DNS message."""
        try:
            parsed = parse_message(wire)
        except DNSProtocolError as exc:
            return ResolutionResult(None, "none", str(exc))

        question = parsed.question
        if question is None:
            return ResolutionResult(None, "none", "message has no question section")

        route_doh = self.should_route_to_doh(question.name)

        if route_doh:
            try:
                response = self.doh.query(wire)
            except Exception as exc:  # noqa: BLE001 - every upstream failure is handled
                if not self._can_use_system(question.qtype):
                    return ResolutionResult(None, "none", f"dns-over-https failed: {exc}")
                fallback = self._resolve_via_system(wire)
                if fallback.ok:
                    return fallback
                return ResolutionResult(None, "none", f"dns-over-https failed: {exc}")
            return ResolutionResult(response, "doh")

        if self.system is None:
            # Without a system resolver every query is sent over DoH, which
            # still works but leaks the routing decision to the provider.
            try:
                return ResolutionResult(self.doh.query(wire), "doh")
            except Exception as exc:  # noqa: BLE001
                return ResolutionResult(None, "none", str(exc))

        fallback = self._resolve_via_system(wire)
        if fallback.ok:
            return fallback
        if not self.system_fallback:
            return fallback
        try:
            return ResolutionResult(self.doh.query(wire), "doh")
        except Exception as exc:  # noqa: BLE001
            return ResolutionResult(None, "none", str(exc))

    def _can_use_system(self, qtype: int) -> bool:
        return self.system is not None and self.system.supports_type(qtype)

    def _resolve_via_system(self, wire: bytes) -> ResolutionResult:
        if self.system is None:
            return ResolutionResult(None, "none", "no system resolver configured")
        try:
            response = self.system.query(wire)
        except Exception as exc:  # noqa: BLE001
            return ResolutionResult(None, "none", f"system resolver failed: {exc}")
        if response is None:
            return ResolutionResult(None, "none", "system resolver cannot answer this query")
        return ResolutionResult(response, "system")

    # -- name based helpers ----------------------------------------------

    def resolve_name(self, name: str, qtype: int = 1) -> list[str]:
        """Resolve ``name`` to a list of addresses."""
        wire = build_query(name, qtype=qtype)
        result = self.resolve_wire(wire)
        if result.wire is None:
            return []
        return extract_addresses(result.wire)

    def resolve_addresses(self, name: str) -> list[str]:
        """Resolve ``name`` for both IPv4 and IPv6."""
        addresses = self.resolve_name(name, qtype=1)
        addresses.extend(self.resolve_name(name, qtype=28))
        # Preserve order while removing duplicates.
        seen: set[str] = set()
        unique: list[str] = []
        for address in addresses:
            if address not in seen:
                seen.add(address)
                unique.append(address)
        return unique
