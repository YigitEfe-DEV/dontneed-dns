"""Tests of the forwarding to the operating system resolver.

The Windows edition always uses the ``getaddrinfo`` resolver, because
Windows has no ``/etc/resolv.conf``.  These tests cover that path, which
the rest of the suite never reaches on Linux.
"""

from __future__ import annotations

import socket

import pytest

from dontneed_dns.core.constants import DNS_TYPE_A, DNS_TYPE_AAAA, DNS_TYPE_CNAME
from dontneed_dns.core.dnsmessage import build_query, parse_message
from dontneed_dns.core.upstream import (
    GetAddrInfoResolver,
    UdpSystemResolver,
    create_system_resolver,
)


@pytest.fixture(autouse=True)
def _restore_the_default_timeout():
    """The default timeout is a process wide setting.

    Every test of this module restores it, so that a test cannot change
    the behaviour of the tests that come after it.
    """
    before = socket.getdefaulttimeout()
    yield
    socket.setdefaulttimeout(before)


def test_the_windows_resolver_answers_with_an_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """The system fallback of Windows synthesises an A record."""
    resolver = GetAddrInfoResolver(timeout=1.0)

    def fake_getaddrinfo(host, port, family, type, proto, flags):
        assert host == "discord.com"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.10", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    response = resolver.query(build_query("discord.com", qtype=DNS_TYPE_A))
    assert response is not None
    parsed = parse_message(response)
    assert parsed.header.rcode == 0
    assert parsed.header.ancount == 1


def test_the_windows_resolver_answers_only_with_a_and_aaaa() -> None:
    """Windows cannot answer a question of another type.

    The Windows resolver cannot be queried with a raw DNS message, so only
    the two address types are synthesised.  Every other type is answered
    over DNS-over-HTTPS.
    """
    resolver = GetAddrInfoResolver(timeout=1.0)
    assert resolver.supports_type(DNS_TYPE_A) is True
    assert resolver.supports_type(DNS_TYPE_AAAA) is True
    assert resolver.supports_type(DNS_TYPE_CNAME) is False
    assert resolver.query(build_query("discord.com", qtype=DNS_TYPE_CNAME)) is None


def test_a_name_that_does_not_exist_is_answered_without_an_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed lookup is an empty answer, not an error.

    The client asked for a name that does not exist; the answer has to say
    so, otherwise the request would be answered with ``SERVFAIL``.
    """
    resolver = GetAddrInfoResolver(timeout=1.0)

    def fake_getaddrinfo(*args, **kwargs):
        raise socket.gaierror("the name does not exist")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    response = resolver.query(build_query("nothing.example", qtype=DNS_TYPE_A))
    assert response is not None
    parsed = parse_message(response)
    assert parsed.header.rcode == 0
    assert parsed.header.ancount == 0


def test_the_resolver_restores_the_default_timeout() -> None:
    """The resolver must not change the process for good.

    The Windows resolver is the only one that is used on Windows, and it
    changes the default timeout of the process for the name lookup.  If it
    is not restored, every later socket of the process inherits it.
    """
    before = socket.getdefaulttimeout()
    socket.setdefaulttimeout(None)
    try:
        expected = socket.getdefaulttimeout()
        resolver = GetAddrInfoResolver(timeout=7.0)
        assert socket.getdefaulttimeout() == 7.0
        resolver.close()
        assert socket.getdefaulttimeout() == expected
    finally:
        socket.setdefaulttimeout(before)


def test_an_explicit_default_timeout_is_not_changed() -> None:
    """A default that somebody else set is left alone."""
    before = socket.getdefaulttimeout()
    socket.setdefaulttimeout(12.0)
    try:
        resolver = GetAddrInfoResolver(timeout=7.0)
        assert socket.getdefaulttimeout() == 12.0
        resolver.close()
        assert socket.getdefaulttimeout() == 12.0
    finally:
        socket.setdefaulttimeout(before)


def test_the_resolver_of_the_platform_is_chosen_by_the_nameservers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A machine with a resolv.conf uses the forwarding resolver."""
    import dontneed_dns.core.upstream as upstream

    monkeypatch.setattr(upstream, "read_resolv_conf_nameservers", lambda *a, **k: ["192.0.2.1"])
    assert isinstance(create_system_resolver(timeout=1.0), UdpSystemResolver)

    monkeypatch.setattr(upstream, "read_resolv_conf_nameservers", lambda *a, **k: [])
    assert isinstance(create_system_resolver(timeout=1.0), GetAddrInfoResolver)
