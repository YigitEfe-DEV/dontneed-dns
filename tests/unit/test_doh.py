"""Tests of the DNS-over-HTTPS client."""

from __future__ import annotations

import ssl

import pytest

from dontneed_dns.core.doh import DEFAULT_PROVIDERS, DohClient, DohProvider, select_providers
from dontneed_dns.core.errors import UpstreamError
from tests.conftest import build_a_response


def test_default_providers_are_unique_and_secure() -> None:
    addresses = [provider.address for provider in DEFAULT_PROVIDERS]
    assert len(addresses) == len(set(addresses))
    for provider in DEFAULT_PROVIDERS:
        assert provider.hostname
        assert provider.path == "/dns-query"


def test_client_uses_the_first_working_provider(provider, doh_transport) -> None:
    transport = doh_transport()
    client = DohClient([provider], transport=transport)
    query = bytes(range(12))
    response = client.query(query)
    assert response
    assert transport.calls == [query]


def test_client_falls_back_to_the_next_provider(provider) -> None:
    calls: list[str] = []

    def transport(current, body, timeout, context):
        calls.append(current.name)
        if len(calls) == 1:
            raise OSError("connection refused")
        return build_a_response(body)

    providers = (
        DohProvider("first", "192.0.2.1", "first.example"),
        DohProvider("second", "192.0.2.2", "second.example"),
    )
    client = DohClient(providers, transport=transport)
    assert client.query(bytes(12))
    assert calls == ["first", "second"]


def test_client_raises_when_every_provider_fails(provider) -> None:
    def transport(current, body, timeout, context):
        raise TimeoutError("timed out")

    client = DohClient([provider], transport=transport, timeout=0.1, retries=0)
    with pytest.raises(UpstreamError):
        client.query(bytes(12))


def test_client_rejects_an_empty_message(provider) -> None:
    client = DohClient([provider])
    with pytest.raises(UpstreamError):
        client.query(b"")


def test_client_requires_at_least_one_provider() -> None:
    with pytest.raises(ValueError):
        DohClient([])


def test_client_bounds_the_number_of_attempts(provider) -> None:
    attempts = 0

    def transport(current, body, timeout, context):
        nonlocal attempts
        attempts += 1
        raise OSError("nope")

    client = DohClient([provider], transport=transport, timeout=0.05, retries=2)
    with pytest.raises(UpstreamError):
        client.query(bytes(12))
    assert attempts <= client.max_attempts


def test_client_resolves_addresses(provider, doh_transport) -> None:
    client = DohClient([provider], transport=doh_transport())
    addresses = client.resolve("discord.com")
    assert addresses == ["203.0.113.10"]


def test_client_verifies_tls_certificates() -> None:
    client = DohClient([DohProvider("test", "192.0.2.1", "dns.test.example")])
    context = client._ssl_context
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_select_providers_resolves_names() -> None:
    selected = select_providers(["cloudflare", "google"])
    assert [provider.name for provider in selected] == ["cloudflare", "google"]


def test_select_providers_rejects_unknown_names() -> None:
    with pytest.raises(ValueError, match="unknown DNS-over-HTTPS provider"):
        select_providers(["nope"])


def test_select_providers_requires_at_least_one() -> None:
    with pytest.raises(ValueError):
        select_providers([])
