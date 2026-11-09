"""Tests of the health checks.

The checks are the only thing that tells the user whether the components
work.  A check that passes while the tool does not do what it is meant to
do is worse than no check at all.
"""

from __future__ import annotations

import pytest

from dontneed_dns.core.config import Config
from dontneed_dns.core.health import Severity, run_health_checks


@pytest.fixture
def config() -> Config:
    """A configuration with the default values."""
    return Config()


def _check(checks, name: str):
    """The check with the given name."""
    for check in checks:
        if check.name == name:
            return check
    raise AssertionError(f"there is no check called {name!r}: {[c.name for c in checks]}")


def test_a_split_domain_answered_over_doh_is_reported(monkeypatch: pytest.MonkeyPatch, config) -> None:
    """The check has to see where the answer came from."""
    from dontneed_dns.core import health

    monkeypatch.setattr(health, "_doh_probe", lambda config, timeout: (True, "answered over doh"))

    checks = run_health_checks(config)

    assert _check(checks, "DNS-over-HTTPS").ok is True


def test_a_split_domain_answered_by_the_system_resolver_is_reported(
    monkeypatch: pytest.MonkeyPatch, config
) -> None:
    """The plain text lookup is the failure this project exists to prevent.

    A split domain that is answered by the system resolver means that the
    query went out in the clear, even though the local resolver answered.
    """
    from dontneed_dns.core import health

    monkeypatch.setattr(
        health,
        "_doh_probe",
        lambda config, timeout: (False, "discord.com was answered by the system resolver"),
    )

    checks = run_health_checks(config)

    check = _check(checks, "DNS-over-HTTPS")
    assert check.ok is False
    assert check.severity == Severity.WARNING
    assert "system resolver" in check.detail


def test_a_provider_that_cannot_be_reached_is_reported(monkeypatch: pytest.MonkeyPatch, config) -> None:
    from dontneed_dns.core import health

    monkeypatch.setattr(health, "_doh_probe", lambda config, timeout: (False, "the providers failed"))

    checks = run_health_checks(config)

    assert _check(checks, "DNS-over-HTTPS").ok is False


def test_the_probe_asks_the_configured_providers(monkeypatch: pytest.MonkeyPatch, config) -> None:
    """The probe has to use the providers of the configuration."""
    from dontneed_dns.core import health
    from dontneed_dns.core.doh import DohClient

    seen: list[str] = []

    class FakeClient(DohClient):
        def __init__(self, providers, **kwargs):
            seen.extend(provider.name for provider in providers)
            super().__init__(providers, **kwargs)

        def query(self, wire):
            return bytes(12)

    monkeypatch.setattr("dontneed_dns.core.doh.DohClient", FakeClient)
    monkeypatch.setattr(
        "dontneed_dns.core.resolver.create_system_resolver", lambda **kwargs: None, raising=False
    )
    monkeypatch.setattr("dontneed_dns.core.upstream.create_system_resolver", lambda **kwargs: None)

    ok, detail = health._doh_probe(config, 1.0)

    assert seen == ["cloudflare", "google", "quad9"]
    assert ok is True
    assert "DNS-over-HTTPS" in detail
