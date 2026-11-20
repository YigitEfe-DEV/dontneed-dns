"""The transparent routing tests in a real network namespace.

The tests run the harness in an unprivileged user and network namespace.
The nftables rules, the routes and the TUN device are really created,
and the traffic really takes the redirected path.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not sys.platform.startswith("linux"),
        reason="the transparent router is only tested on Linux",
    ),
]

ROOT = Path(__file__).resolve().parent.parent.parent
HARNESS = ROOT / "tests" / "integration" / "netns_harness.py"


def _unavailable() -> str:
    """The reason why the namespace cannot be used, if it cannot."""
    if not Path("/dev/net/tun").exists():
        return "the TUN device is not available"
    if shutil_which("nft") is None:
        return "nftables is not available"
    result = subprocess.run(["unshare", "-Urn", "true"], capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        return f"the network namespace cannot be created: {result.stderr.strip()}"
    return ""


def shutil_which(name: str) -> str | None:
    """The path of a program, if it is installed."""
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(directory) / name
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


@pytest.fixture(scope="module")
def namespace_results() -> dict:
    """Run the harness and return its results."""
    reason = _unavailable()
    if reason:
        pytest.skip(reason)
    result = subprocess.run(
        ["unshare", "-Urn", sys.executable, str(HARNESS)],
        capture_output=True,
        text=True,
        timeout=600,
        cwd=str(ROOT),
    )
    document: dict = {}
    for line in result.stdout.splitlines():
        if line.startswith("RESULT "):
            document = json.loads(line[len("RESULT ") :])
    if not document:
        pytest.fail(f"the harness did not report a result:\n{result.stdout}\n{result.stderr}")
    document["_returncode"] = result.returncode
    document["_stderr"] = result.stderr
    return document


def test_the_harness_ran(namespace_results: dict) -> None:
    """The harness itself must not fail."""
    assert "error" not in namespace_results, namespace_results.get("error")
    assert namespace_results["_returncode"] == 0, namespace_results["_stderr"]


def test_the_tcp_traffic_is_redirected(namespace_results: dict) -> None:
    """A TCP connection to a target goes through the proxy."""
    assert namespace_results.get("tcp_ok") is True, namespace_results
    assert namespace_results.get("tcp_answer") == "echo:hello"


def test_the_ipv6_tcp_traffic_is_redirected(namespace_results: dict) -> None:
    """The same over IPv6."""
    assert namespace_results.get("tcp6_ok") is True, namespace_results
    assert namespace_results.get("tcp6_answer") == "echo:hello"


def test_the_udp_traffic_is_forwarded(namespace_results: dict) -> None:
    """A datagram to a target goes through the relay."""
    assert namespace_results.get("udp_ok") is True, namespace_results
    assert namespace_results.get("udp_answer") == "echo:ping"


def test_the_ipv6_udp_traffic_is_forwarded(namespace_results: dict) -> None:
    """The same over IPv6."""
    assert namespace_results.get("udp6_ok") is True, namespace_results
    assert namespace_results.get("udp6_answer") == "echo:ping"


def test_the_dns_queries_are_redirected(namespace_results: dict) -> None:
    """A DNS query of the user is answered by the local resolver."""
    assert namespace_results.get("dns_ok") is True, namespace_results
    assert namespace_results.get("dns_rcode") == 0


def test_the_fallback_of_the_resolver_is_not_redirected(namespace_results: dict) -> None:
    """The resolver must not redirect its own fallback queries."""
    assert namespace_results.get("dns_fallback_ok") is True, namespace_results
    assert namespace_results.get("dns_fallback_address") == ["10.0.0.9"]


def test_the_rules_are_removed_when_the_router_stops(namespace_results: dict) -> None:
    """The router leaves nothing behind."""
    assert namespace_results.get("project_tables_removed") is True
    assert namespace_results.get("rules_removed") is True


def test_the_rules_of_other_programs_are_kept(namespace_results: dict) -> None:
    """The firewall of the machine is not touched."""
    assert namespace_results.get("foreign_table_kept") is True
