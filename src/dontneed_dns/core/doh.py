"""DNS-over-HTTPS client (RFC 8484).

Design notes
------------
* The client connects to the provider **by IP address** so that it never
  depends on the very resolver it is meant to replace.  A local DNS server
  pointing at ourselves would otherwise deadlock.
* TLS certificates are still verified against the provider *hostname*
  (``server_hostname``), so verification is never weakened by using an IP
  literal as the connect target.
* The total time spent on one query is bounded by ``timeout * (retries + 1)``
  regardless of how many providers are configured.
"""

from __future__ import annotations

import itertools
import socket
import ssl
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from dontneed_dns import USER_AGENT
from dontneed_dns.core.constants import (
    DEFAULT_DOH_RETRIES,
    DEFAULT_DOH_TIMEOUT,
    DNS_TYPE_A,
)
from dontneed_dns.core.dnsmessage import build_query, extract_addresses
from dontneed_dns.core.errors import UpstreamError

__all__ = ["DEFAULT_PROVIDERS", "DohClient", "DohProvider"]


@dataclass(frozen=True)
class DohProvider:
    """A DNS-over-HTTPS endpoint."""

    name: str
    address: str
    hostname: str
    path: str = "/dns-query"

    @property
    def url(self) -> str:
        """The human readable URL of this endpoint."""
        return f"https://{self.hostname}{self.path}"


DEFAULT_PROVIDERS: tuple[DohProvider, ...] = (
    DohProvider("cloudflare", "1.1.1.1", "cloudflare-dns.com"),
    DohProvider("google", "8.8.8.8", "dns.google"),
    DohProvider("quad9", "9.9.9.9", "dns.quad9.net"),
)

_PROVIDER_BY_NAME: dict[str, DohProvider] = {p.name: p for p in DEFAULT_PROVIDERS}


def provider_by_name(name: str) -> DohProvider | None:
    """Look up a bundled provider by its short name."""
    return _PROVIDER_BY_NAME.get(name.strip().lower())


Transport = Callable[[DohProvider, bytes, float, ssl.SSLContext], bytes]


def _default_ssl_context() -> ssl.SSLContext:
    """A strict TLS context: certificates are required and verified."""
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def _read_headers(stream) -> tuple[str, dict[str, str]]:
    """Read the status line and headers from a socket file object."""
    status_line = stream.readline().decode("latin-1").strip()
    headers: dict[str, str] = {}
    while True:
        line = stream.readline().decode("latin-1")
        if line in ("\r\n", "\n", ""):
            break
        if ":" not in line:
            continue
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    return status_line, headers


def _read_body(stream, headers: dict[str, str]) -> bytes:
    """Read an HTTP body honouring ``Content-Length`` and chunked encoding."""
    encoding = headers.get("transfer-encoding", "").lower()
    if "chunked" in encoding:
        chunks: list[bytes] = []
        while True:
            size_line = stream.readline().decode("latin-1").strip()
            if not size_line:
                break
            try:
                size = int(size_line.split(";", 1)[0], 16)
            except ValueError as exc:
                raise UpstreamError("invalid chunk size in upstream response") from exc
            if size == 0:
                stream.readline()
                break
            chunks.append(stream.read(size))
            stream.readline()
        return b"".join(chunks)

    length = headers.get("content-length")
    if length is not None:
        try:
            return stream.read(int(length))
        except ValueError as exc:
            raise UpstreamError("invalid content-length in upstream response") from exc
    return stream.read()


def _https_post(
    provider: DohProvider,
    body: bytes,
    timeout: float,
    context: ssl.SSLContext,
) -> bytes:
    """POST a DNS message to ``provider`` and return the raw DNS response."""
    raw_socket = socket.create_connection((provider.address, 443), timeout=timeout)
    try:
        raw_socket.settimeout(timeout)
        tls_socket = context.wrap_socket(raw_socket, server_hostname=provider.hostname)
    except Exception:
        raw_socket.close()
        raise
    with tls_socket:
        request = (
            f"POST {provider.path} HTTP/1.1\r\n"
            f"Host: {provider.hostname}\r\n"
            f"Content-Type: application/dns-message\r\n"
            f"Accept: application/dns-message\r\n"
            f"Accept-Encoding: identity\r\n"
            f"User-Agent: {USER_AGENT}\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n"
            "\r\n"
        ).encode("ascii")
        tls_socket.sendall(request + body)

        with tls_socket.makefile("rb") as stream:
            status_line, headers = _read_headers(stream)
            if not status_line:
                raise UpstreamError(f"{provider.name}: empty response")
            parts = status_line.split(" ", 2)
            if len(parts) < 2 or not parts[1].isdigit():
                raise UpstreamError(f"{provider.name}: malformed status line")
            status = int(parts[1])
            payload = _read_body(stream, headers)

    if status != 200:
        raise UpstreamError(f"{provider.name}: upstream returned HTTP {status}")
    if not payload:
        raise UpstreamError(f"{provider.name}: empty DNS response")
    return payload


class DohClient:
    """A resilient DNS-over-HTTPS client with a bounded total runtime."""

    def __init__(
        self,
        providers: Sequence[DohProvider] | None = None,
        *,
        timeout: float = DEFAULT_DOH_TIMEOUT,
        retries: int = DEFAULT_DOH_RETRIES,
        transport: Transport | None = None,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self.providers: tuple[DohProvider, ...] = tuple(
            providers if providers is not None else DEFAULT_PROVIDERS
        )
        if not self.providers:
            raise ValueError("at least one DNS-over-HTTPS provider is required")
        self.timeout = timeout
        self.retries = max(0, retries)
        self._transport = transport or _https_post
        self._ssl_context = ssl_context or _default_ssl_context()

    @property
    def max_attempts(self) -> int:
        """The hard upper bound on the number of upstream attempts."""
        return len(self.providers) * (self.retries + 1)

    def query(self, wire: bytes) -> bytes:
        """Resolve a raw DNS message and return the raw DNS response.

        Raises :class:`UpstreamError` when every attempt failed.
        """
        if not wire:
            raise UpstreamError("empty DNS message")

        deadline = time.monotonic() + self.timeout * (self.retries + 1)
        cycle = itertools.cycle(self.providers)
        failures: list[str] = []
        attempts = 0

        while attempts < self.max_attempts:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            provider = next(cycle)
            attempts += 1
            try:
                return self._transport(provider, wire, min(self.timeout, remaining), self._ssl_context)
            except Exception as exc:  # noqa: BLE001 - every failure is retried
                failures.append(f"{provider.name}: {exc}")

        detail = failures[-1] if failures else "no provider available"
        raise UpstreamError(f"all DNS-over-HTTPS providers failed (last error: {detail})")

    def resolve(self, name: str, qtype: int = DNS_TYPE_A) -> list[str]:
        """Resolve ``name`` and return the list of addresses."""
        response = self.query(build_query(name, qtype=qtype))
        return extract_addresses(response)

    def resolve_both(self, name: str) -> list[str]:
        """Resolve ``name`` for both IPv4 and IPv6."""
        addresses = self.resolve(name, qtype=DNS_TYPE_A)
        addresses.extend(self.resolve(name, qtype=28))
        return addresses


def select_providers(names: Iterable[str]) -> tuple[DohProvider, ...]:
    """Resolve provider short names to :class:`DohProvider` objects."""
    selected: list[DohProvider] = []
    for name in names:
        provider = provider_by_name(name)
        if provider is None:
            known = ", ".join(sorted(_PROVIDER_BY_NAME))
            raise ValueError(f"unknown DNS-over-HTTPS provider {name!r}; known providers: {known}")
        if provider not in selected:
            selected.append(provider)
    if not selected:
        raise ValueError("at least one DNS-over-HTTPS provider must be enabled")
    return tuple(selected)
