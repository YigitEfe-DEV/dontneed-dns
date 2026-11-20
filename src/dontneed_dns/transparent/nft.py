"""Management of the nftables rules of the transparent router.

The project owns exactly two tables, ``ip dontneed_dns`` and
``ip6 dontneed_dns``.  Every rule lives in them; nothing else is read,
written or deleted.  The rules of the host, including the ones of
firewalld, are never touched, and the tables are removed as a whole when
the router stops.

The rules redirect the traffic of one user to the local transparent
proxies:

* DNS queries go to the local split resolver,
* TCP connections to the target addresses go to the transparent proxy,
* UDP datagrams to the target addresses go to the transparent relay.

Traffic that the router itself produces carries a mark, and every rule
returns early for marked packets.  That is what prevents the routing loop:
the outbound connections of the router must not be redirected again.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "NftError",
    "NftManager",
    "TargetSet",
]


class NftError(Exception):
    """An nftables command failed."""


@dataclass
class TargetSet:
    """The addresses that are routed through the transparent router."""

    ipv4: set[str] = field(default_factory=set)
    ipv6: set[str] = field(default_factory=set)

    def add(self, address: str) -> bool:
        """Add one address.  Returns ``True`` when the set changed."""
        import ipaddress

        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            return False
        text = str(parsed)
        if parsed.version == 4:
            if text in self.ipv4:
                return False
            self.ipv4.add(text)
            return True
        if text in self.ipv6:
            return False
        self.ipv6.add(text)
        return True

    def add_many(self, addresses: list[str] | tuple[str, ...]) -> bool:
        """Add several addresses.  Returns ``True`` when the set changed."""
        changed = False
        for address in addresses:
            # Every address is added; the loop must not stop at the first
            # change.
            changed = self.add(address) or changed
        return changed

    def clear(self) -> None:
        """Forget every address."""
        self.ipv4.clear()
        self.ipv6.clear()

    def as_json(self) -> list[str]:
        """The addresses as a sorted list."""
        return sorted(self.ipv4) + sorted(self.ipv6)


class NftManager:
    """Installs, updates and removes the rules of the transparent router."""

    def __init__(
        self,
        *,
        table: str = "dontneed_dns",
        uid: int | None = None,
        dns_port: int = 5354,
        tcp_port: int = 15001,
        udp_port: int = 15002,
        mark: int = 1,
        nft_binary: str = "nft",
        intercept_dns: bool = True,
        intercept_tcp: bool = True,
        intercept_udp: bool = True,
        resolver_source_port: int = 0,
    ) -> None:
        self.table = table
        self.uid = uid
        self.dns_port = dns_port
        self.tcp_port = tcp_port
        self.udp_port = udp_port
        self.mark = mark
        self.nft_binary = nft_binary
        self.intercept_dns = intercept_dns
        self.intercept_tcp = intercept_tcp
        self.intercept_udp = intercept_udp
        #: The port the fallback queries of the resolver are sent from.
        self.resolver_source_port = resolver_source_port
        self.targets = TargetSet()

    # -- naming -----------------------------------------------------------

    @property
    def table_v4(self) -> str:
        """The name of the IPv4 table."""
        return self.table

    @property
    def table_v6(self) -> str:
        """The name of the IPv6 table."""
        return f"{self.table}6"

    # -- rendering --------------------------------------------------------

    def _owner(self) -> str:
        """The matcher that restricts the rules to the owner of the router."""
        if self.uid is None:
            return ""
        return f"meta skuid {self.uid} "

    def _skip_mark(self) -> str:
        """The rule that lets the traffic of the router pass."""
        return f"meta mark 0x{self.mark:x} return"

    def render_v4(self) -> str:
        """Render the IPv4 ruleset."""
        owner = self._owner()
        lines = [
            f"table ip {self.table_v4} {{",
            "\tset targets {",
            "\t\ttype ipv4_addr",
            "\t\tflags interval",
        ]
        if self.targets.ipv4:
            lines.append("\t\telements = { " + ", ".join(sorted(self.targets.ipv4)) + " }")
        lines.extend(["\t}", ""])
        prerouting = [
            "\tchain prerouting {",
            "\t\ttype nat hook prerouting priority dstnat; policy accept;",
            f"\t\t{self._skip_mark()}",
        ]
        output = [
            "\tchain output {",
            "\t\ttype nat hook output priority dstnat; policy accept;",
            f"\t\t{self._skip_mark()}",
        ]
        if self.intercept_dns:
            for chain in (prerouting, output):
                if self.resolver_source_port:
                    # The fallback queries of the resolver are not
                    # redirected, otherwise the resolver would redirect
                    # its own queries.
                    chain.append(f"\t\t{owner}udp sport {self.resolver_source_port} return")
                chain.append(f"\t\t{owner}udp dport 53 dnat to 127.0.0.1:{self.dns_port}")
                chain.append(f"\t\t{owner}tcp dport 53 dnat to 127.0.0.1:{self.dns_port}")
        if self.intercept_tcp:
            for chain in (prerouting, output):
                chain.append(
                    f"\t\t{owner}ip daddr @targets meta l4proto tcp dnat to 127.0.0.1:{self.tcp_port}"
                )
        # The UDP traffic is not redirected here.  A redirect destroys the
        # address the datagram was sent to, and the receiver needs it.  It
        # is routed into the TUN device instead.
        for chain in (prerouting, output):
            chain.append("\t}")
            lines.extend(chain)
            lines.append("")
        lines.append("}")
        return "\n".join(lines) + "\n"

    def render_v6(self) -> str:
        """Render the IPv6 ruleset."""
        owner = self._owner()
        lines = [
            f"table ip6 {self.table_v6} {{",
            "\tset targets {",
            "\t\ttype ipv6_addr",
            "\t\tflags interval",
        ]
        if self.targets.ipv6:
            lines.append("\t\telements = { " + ", ".join(sorted(self.targets.ipv6)) + " }")
        lines.extend(["\t}", ""])
        prerouting = [
            "\tchain prerouting {",
            "\t\ttype nat hook prerouting priority dstnat; policy accept;",
            f"\t\t{self._skip_mark()}",
        ]
        output = [
            "\tchain output {",
            "\t\ttype nat hook output priority dstnat; policy accept;",
            f"\t\t{self._skip_mark()}",
        ]
        if self.intercept_dns:
            for chain in (prerouting, output):
                if self.resolver_source_port:
                    chain.append(f"\t\t{owner}udp sport {self.resolver_source_port} return")
                chain.append(f"\t\t{owner}udp dport 53 dnat to [::1]:{self.dns_port}")
                chain.append(f"\t\t{owner}tcp dport 53 dnat to [::1]:{self.dns_port}")
        if self.intercept_tcp:
            for chain in (prerouting, output):
                chain.append(f"\t\t{owner}ip6 daddr @targets meta l4proto tcp dnat to [::1]:{self.tcp_port}")
        # The UDP traffic of the targets is routed into the TUN device.
        for chain in (prerouting, output):
            chain.append("\t}")
            lines.extend(chain)
            lines.append("")
        lines.append("}")
        return "\n".join(lines) + "\n"

    def render(self) -> str:
        """Render both tables."""
        return self.render_v4() + "\n" + self.render_v6()

    # -- commands ---------------------------------------------------------

    def _run(self, arguments: list[str], *, stdin: str | None = None):
        from dontneed_dns.core.system import run_command

        result = run_command([self.nft_binary, *arguments], timeout=30.0, stdin=stdin)
        if not result.ok:
            raise NftError(result.summary() + (f"\n{result.output()}" if result.output() else ""))
        return result

    def is_available(self) -> bool:
        """``True`` when nftables can be used."""
        from dontneed_dns.core.system import which

        if which(self.nft_binary) is None:
            return False
        try:
            result = self._run(["--version"])
        except NftError:
            return False
        return bool(result)

    def has_permission(self) -> bool:
        """``True`` when the rules can be installed."""
        from dontneed_dns.core.system import run_command

        result = run_command([self.nft_binary, "list", "tables"], timeout=15.0)
        return result.ok

    def apply(self) -> None:
        """Install the rules.  The existing tables are replaced."""
        script = f"delete table ip {self.table_v4}\n" if self.table_exists_v4() else ""
        if self.table_exists_v6():
            script += f"delete table ip6 {self.table_v6}\n"
        script += self.render()
        self._run(["-f", "-"], stdin=script)

    def _table_exists(self, family: str, name: str) -> bool:
        from dontneed_dns.core.system import run_command

        result = run_command([self.nft_binary, "list", "tables"], timeout=15.0)
        if not result.ok:
            return False
        return f"{family} {name}" in result.stdout or f"{family} {name} " in result.stdout

    def table_exists_v4(self) -> bool:
        """``True`` when the IPv4 table exists."""
        return self._table_exists("ip", self.table_v4)

    def table_exists_v6(self) -> bool:
        """``True`` when the IPv6 table exists."""
        return self._table_exists("ip6", self.table_v6)

    def remove(self) -> None:
        """Remove both tables.  Missing tables are not an error."""
        for family, name in (("ip", self.table_v4), ("ip6", self.table_v6)):
            try:
                self._run(["delete", "table", family, name])
            except NftError:
                continue

    def sync_targets(self) -> bool:
        """Write the current target addresses into the sets.

        Returns ``True`` when something changed.
        """
        changed = False
        for family, name, addresses in (
            ("ip", self.table_v4, sorted(self.targets.ipv4)),
            ("ip6", self.table_v6, sorted(self.targets.ipv6)),
        ):
            if not addresses:
                continue
            try:
                self._run(["add", "element", family, name, "targets", "{ " + ", ".join(addresses) + " }"])
            except NftError:
                continue
            changed = True
        return changed

    def clear_targets(self) -> None:
        """Remove every element from the sets."""
        for family, name in (("ip", self.table_v4), ("ip6", self.table_v6)):
            try:
                self._run(["flush", "set", family, name, "targets"])
            except NftError:
                continue

    def status(self) -> dict[str, object]:
        """Report the state of the rules."""
        from dontneed_dns.core.system import run_command

        result = run_command([self.nft_binary, "list", "tables"], timeout=15.0)
        tables = result.stdout if result.ok else ""
        return {
            "available": self.is_available(),
            "permission": result.ok,
            "table_v4": self.table_v4,
            "table_v6": self.table_v6,
            "installed": self.table_v4 in tables,
            "targets": self.targets.as_json(),
        }
