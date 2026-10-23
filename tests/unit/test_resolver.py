"""Tests of the split resolver."""

from __future__ import annotations

from dontneed_dns.core.dnsmessage import build_query, parse_message
from dontneed_dns.core.doh import DohClient, DohProvider
from dontneed_dns.core.resolver import SplitResolver
from dontneed_dns.core.upstream import SystemResolver
from tests.conftest import build_a_response


class FakeSystemResolver(SystemResolver):
    """A system resolver that always answers."""

    def __init__(self, response: bytes | None = None) -> None:
        self.response = response
        self.queries: list[bytes] = []

    def query(self, wire: bytes) -> bytes | None:
        self.queries.append(wire)
        if self.response is None:
            return build_a_response(wire, address="198.51.100.5")
        return self.response


def make_resolver(transport, system=None, **kwargs) -> SplitResolver:
    provider = DohProvider("test", "192.0.2.1", "dns.test.example")
    return SplitResolver(
        DohClient([provider], transport=transport),
        system,
        split_domains=kwargs.pop("split_domains", ("discord.com",)),
        **kwargs,
    )


def test_split_domain_goes_to_doh(doh_transport) -> None:
    transport = doh_transport()
    system = FakeSystemResolver()
    resolver = make_resolver(transport, system)

    result = resolver.resolve_wire(build_query("cdn.discord.com"))

    assert result.ok
    assert result.source == "doh"
    assert transport.calls
    assert system.queries == []


def test_other_domain_goes_to_the_system_resolver(doh_transport) -> None:
    transport = doh_transport()
    system = FakeSystemResolver()
    resolver = make_resolver(transport, system)

    result = resolver.resolve_wire(build_query("example.org"))

    assert result.ok
    assert result.source == "system"
    assert transport.calls == []
    assert len(system.queries) == 1


def test_route_split_can_be_switched_off(doh_transport) -> None:
    transport = doh_transport()
    system = FakeSystemResolver()
    resolver = make_resolver(transport, system, route_split=False)

    resolver.resolve_wire(build_query("discord.com"))
    assert transport.calls == []


def test_doh_failure_falls_back_to_the_system_resolver(doh_transport) -> None:
    def failing(current, body, timeout, context):
        raise OSError("upstream is down")

    system = FakeSystemResolver()
    resolver = make_resolver(failing, system)

    result = resolver.resolve_wire(build_query("discord.com"))
    assert result.ok
    assert result.source == "system"


def test_doh_failure_without_a_system_resolver_is_reported(doh_transport) -> None:
    def failing(current, body, timeout, context):
        raise OSError("upstream is down")

    resolver = make_resolver(failing, None)
    result = resolver.resolve_wire(build_query("discord.com"))
    assert not result.ok
    assert "dns-over-https failed" in result.error


def test_a_malformed_message_is_rejected(doh_transport) -> None:
    resolver = make_resolver(doh_transport())
    result = resolver.resolve_wire(b"\x00\x01")
    assert not result.ok


def test_resolve_addresses_deduplicates(doh_transport) -> None:
    transport = doh_transport()
    resolver = make_resolver(transport)
    addresses = resolver.resolve_addresses("discord.com")
    assert addresses
    assert len(addresses) == len(set(addresses))


def test_the_answer_keeps_the_request_identifier(doh_transport) -> None:
    resolver = make_resolver(doh_transport())
    query = build_query("discord.com")
    result = resolver.resolve_wire(query)
    assert result.wire is not None
    parsed = parse_message(result.wire)
    assert parsed.header.ident == parse_message(query).header.ident
