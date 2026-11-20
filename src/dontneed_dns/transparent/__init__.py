"""The transparent router of the Fedora edition.

The router redirects the traffic of the user to the local proxies with
nftables.  It owns two tables, ``ip dontneed_dns`` and ``ip6
dontneed_dns``, and removes them when it stops.  The rules of the host,
including the ones of firewalld, are never touched.
"""

from __future__ import annotations

__all__ = [
    "NftError",
    "NftManager",
    "TargetSet",
    "TransparentRouter",
    "TransparentTcpServer",
    "TransparentUdpRelay",
]

from dontneed_dns.transparent.nft import NftError, NftManager, TargetSet
from dontneed_dns.transparent.service import TransparentRouter
from dontneed_dns.transparent.tcp import TransparentTcpServer
from dontneed_dns.transparent.udp import TransparentUdpRelay
