"""The foreground runner of the transparent router.

The systemd unit starts this module.  It installs the nftables rules and
the routes of the project, starts the transparent TCP proxy and the UDP
relay and keeps the address set up to date.  When the service stops, the
rules and the routes are removed again, so that the network of the
machine is exactly as it was before the service ran.

The traffic is divided as follows:

* the DNS queries of the user are redirected to the local resolver,
  which answers the target domains over DNS-over-HTTPS,
* the TCP connections to the target addresses are redirected to the
  transparent proxy, which can split the TLS ClientHello,
* the UDP traffic of the target addresses is routed into a TUN device.
  A redirect would destroy the address the datagram was sent to, and the
  receiver of a game server needs it.
"""

from __future__ import annotations

import os
import socket
import threading

from dontneed_dns.core.config import Config
from dontneed_dns.core.logging_setup import get_logger
from dontneed_dns.transparent.nft import NftError, NftManager
from dontneed_dns.transparent.targets import TargetWatcher
from dontneed_dns.transparent.tcp import TransparentTcpServer
from dontneed_dns.transparent.tun import RouteManager, TunDevice, TunError
from dontneed_dns.transparent.udp_tun import TunUdpRelay

__all__ = ["TransparentRouter", "run_transparent"]

_LOGGER = get_logger("transparent.service")

#: How often the availability of the resolver is checked.
DNS_GUARD_INTERVAL = 2.0

#: The name of the TUN device of the project.
TUN_NAME = "dnd0"

#: The routing table that holds the routes of the project.
ROUTE_TABLE = 100


class TransparentRouter:
    """The transparent router: the rules, the routes and the proxies."""

    def __init__(self, config: Config) -> None:
        self.config = config
        settings = config.transparent
        self.manager = NftManager(
            table=settings.nft_table,
            uid=os.getuid(),
            dns_port=config.dns.listen_port,
            tcp_port=settings.tcp_port,
            udp_port=settings.udp_port,
            mark=settings.bypass_mark,
            intercept_dns=settings.intercept_dns,
            intercept_tcp=settings.intercept_tcp,
            intercept_udp=False,
            resolver_source_port=settings.resolver_source_port,
        )
        domains = tuple(settings.sni_domains) or tuple(config.dns.split_domains)
        self.tcp = TransparentTcpServer(
            port=settings.tcp_port,
            mark=settings.bypass_mark,
            fragment=settings.fragment,
            fragment_chunk_size=config.proxy.fragment_chunk_size,
            fragment_preamble_delay_ms=config.proxy.fragment_preamble_delay_ms,
            fragment_chunk_delay_ms=config.proxy.fragment_chunk_delay_ms,
            sni_domains=domains,
            connect_timeout=config.proxy.connect_timeout,
            handshake_timeout=config.proxy.handshake_timeout,
            idle_timeout=config.proxy.idle_timeout,
            max_connections=config.proxy.max_connections,
        )
        self.tun = TunDevice(name=TUN_NAME)
        # The mark is the same for the nftables rules and for the routes:
        # the traffic of the router itself is neither redirected nor sent
        # into the TUN device, which prevents the routing loop.
        self.routes = RouteManager(
            tun_name=TUN_NAME,
            table=ROUTE_TABLE,
            uid=os.getuid(),
            mark=settings.bypass_mark,
        )
        self.udp = TunUdpRelay(
            self.tun,
            mark=settings.bypass_mark,
            idle_timeout=min(config.proxy.idle_timeout, 120.0),
        )
        self.watcher: TargetWatcher | None = None
        self._resolver = None
        self._dns_available = True
        self._guard: threading.Thread | None = None
        self._stop = threading.Event()

    # -- helpers ----------------------------------------------------------

    def build_resolver(self):
        """Create the resolver that keeps the target addresses fresh."""
        from dontneed_dns.core.doh import DohClient, select_providers
        from dontneed_dns.core.resolver import SplitResolver

        try:
            providers = select_providers(self.config.dns.doh_providers)
        except ValueError as exc:
            _LOGGER.warning("the target addresses cannot be learned: %s", exc)
            return None
        doh = DohClient(providers, timeout=self.config.dns.timeout, retries=self.config.dns.retries)
        return SplitResolver(
            doh,
            None,
            split_domains=tuple(self.config.dns.split_domains),
            route_split=True,
            system_fallback=False,
        )

    @staticmethod
    def resolver_reachable(config: Config) -> bool:
        """``True`` when the local resolver answers."""
        host = config.dns.listen_host
        port = config.dns.listen_port
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            return False

    # -- lifecycle --------------------------------------------------------

    def check(self) -> str:
        """Check that the router can run.  Returns an empty string on success."""
        if not self.manager.is_available():
            return "nftables is not available on this system"
        if not self.manager.has_permission():
            return "the nftables rules cannot be changed; the service needs the CAP_NET_ADMIN capability"
        return ""

    def start(self) -> None:
        """Install the rules and the routes and start the proxies."""
        problem = self.check()
        if problem:
            raise RuntimeError(problem)

        self.manager.targets.add_many(self.config.transparent.target_ips)
        try:
            self.manager.apply()
        except NftError as exc:
            raise RuntimeError(f"the nftables rules could not be installed: {exc}") from exc

        try:
            self.tun.open()
            self.routes.install_rules()
            self.routes.set_targets(sorted(self.manager.targets.ipv4))
            self.udp.start()
        except (TunError, OSError) as exc:
            self._rollback()
            raise RuntimeError(f"the UDP relay could not be started: {exc}") from exc

        try:
            self.tcp.bind()
        except OSError as exc:
            self._rollback()
            raise RuntimeError(f"the transparent proxy could not be started: {exc}") from exc

        threading.Thread(target=self.tcp.serve_forever, name="transparent-tcp", daemon=True).start()

        if self.config.transparent.learn:
            self._resolver = self.build_resolver()
            self.watcher = TargetWatcher(
                self.manager,
                tuple(self.config.dns.split_domains),
                refresh_seconds=self.config.transparent.refresh_seconds,
                resolver=self._resolver,
                on_change=self._targets_changed,
            )
            self.watcher.start()

        self._guard = threading.Thread(target=self._dns_guard, name="transparent-guard", daemon=True)
        self._guard.start()
        _LOGGER.info(
            "the transparent router is ready: tcp %d, table %s, device %s",
            self.config.transparent.tcp_port,
            self.config.transparent.nft_table,
            TUN_NAME,
        )

    def _targets_changed(self, addresses: list[str]) -> None:
        """Route the new addresses into the TUN device."""
        try:
            self.routes.set_targets(sorted(addresses))
        except TunError as exc:
            _LOGGER.info("the routes could not be updated: %s", exc)

    def _rollback(self) -> None:
        """Remove everything that was installed."""
        try:
            self.routes.remove()
        except TunError as exc:
            _LOGGER.info("the routes could not be removed: %s", exc)
        try:
            self.tun.close()
        except TunError as exc:
            _LOGGER.info("the TUN device could not be closed: %s", exc)
        try:
            self.manager.remove()
        except NftError as exc:
            _LOGGER.info("the nftables rules could not be removed: %s", exc)

    def _dns_guard(self) -> None:
        """Keep the DNS rules only while the resolver answers.

        A redirection to a port nobody listens on would break every DNS
        query of the user, so the rules are removed as soon as the
        resolver disappears.
        """
        while not self._stop.wait(DNS_GUARD_INTERVAL):
            if not self.config.transparent.intercept_dns:
                continue
            available = self.resolver_reachable(self.config)
            if available == self._dns_available:
                continue
            self._dns_available = available
            self.manager.intercept_dns = available
            try:
                self.manager.apply()
            except NftError as exc:
                _LOGGER.info("the nftables rules could not be updated: %s", exc)
                continue
            if available:
                _LOGGER.info("the resolver answers again, the DNS redirection is active")
            else:
                _LOGGER.warning("the resolver does not answer, the DNS redirection was removed")

    def stop(self) -> None:
        """Stop the proxies and remove the rules and the routes."""
        self._stop.set()
        if self.watcher is not None:
            self.watcher.stop()
        self.tcp.shutdown()
        self.udp.stop()
        self._rollback()


def run_transparent(config: Config, stop_event: threading.Event) -> int:
    """Run the transparent router until ``stop_event`` is set."""
    router = TransparentRouter(config)
    try:
        router.start()
    except RuntimeError as exc:
        _LOGGER.error("the transparent router did not start: %s", exc)
        return 1
    try:
        while not stop_event.wait(0.5):
            pass
    finally:
        router.stop()
    return 0
