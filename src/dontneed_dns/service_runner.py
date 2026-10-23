"""Foreground runner used by the platform services.

The systemd units and the Windows scheduled task both start this module.  It
reads the configuration, starts the requested component and shuts it down
cleanly when the service manager asks it to stop.
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
from collections.abc import Sequence
from pathlib import Path

from dontneed_dns.core.config import Config, load_config
from dontneed_dns.core.doh import DohClient, select_providers
from dontneed_dns.core.logging_setup import get_logger, setup_logging
from dontneed_dns.core.paths import pid_file
from dontneed_dns.core.resolver import SplitResolver
from dontneed_dns.core.upstream import create_system_resolver
from dontneed_dns.dns.server import DnsServer
from dontneed_dns.proxy.server import ProxyServer

__all__ = ["build_resolver", "main", "run_dns", "run_proxy"]

_LOGGER = get_logger("service")


def build_resolver(config: Config) -> SplitResolver:
    """Create the split resolver described by ``config``."""
    providers = select_providers(config.dns.doh_providers)
    doh = DohClient(providers, timeout=config.dns.timeout, retries=config.dns.retries)
    system = create_system_resolver(timeout=config.dns.timeout) if config.dns.system_fallback else None
    return SplitResolver(
        doh,
        system,
        split_domains=config.dns.split_domains,
        route_split=config.dns.route_split,
        system_fallback=config.dns.system_fallback,
    )


def _write_pid_file(component: str) -> Path:
    path = pid_file(f"dontneed-dns-{component}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(__import__("os").getpid()), encoding="utf-8")
    except OSError:
        pass
    return path


def _remove_pid_file(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def run_dns(config: Config, stop_event: threading.Event) -> int:
    """Run the DNS server until ``stop_event`` is set."""
    resolver = build_resolver(config)
    server = DnsServer(
        resolver,
        host=config.dns.listen_host,
        port=config.dns.listen_port,
        host_v6=config.dns.listen_host_v6,
        enable_tcp=config.dns.tcp_enabled,
        max_concurrency=config.dns.max_concurrency,
    )
    try:
        server.bind()
    except OSError as exc:
        _LOGGER.error("the DNS listener could not be opened: %s", exc.strerror or exc)
        return 1

    thread = threading.Thread(target=server.serve_forever, name="dns-server", daemon=True)
    thread.start()
    _LOGGER.info("DNS resolver ready on port %d", config.dns.listen_port)

    try:
        while not stop_event.wait(0.5):
            pass
    finally:
        server.shutdown()
    return 0


def run_proxy(config: Config, stop_event: threading.Event) -> int:
    """Run the proxy until ``stop_event`` is set."""
    resolver = build_resolver(config)
    server = ProxyServer(resolver, config.proxy)
    try:
        server.bind()
    except OSError as exc:
        _LOGGER.error("the proxy listener could not be opened: %s", exc.strerror or exc)
        return 1

    thread = threading.Thread(target=server.serve_forever, name="proxy-server", daemon=True)
    thread.start()
    _LOGGER.info("proxy ready on port %d", config.proxy.listen_port)

    try:
        while not stop_event.wait(0.5):
            pass
    finally:
        server.shutdown()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point of the service runner."""
    parser = argparse.ArgumentParser(
        prog="dontneed-dns-service",
        description="Run a dontneed-dns component in the foreground.",
    )
    parser.add_argument("component", choices=["dns", "proxy"], help="the component to run")
    parser.add_argument("--config", default=None, help="path of the configuration file")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except Exception as exc:  # noqa: BLE001
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    setup_logging(config)

    stop_event = threading.Event()

    def _handle_signal(signum: int, _frame: object) -> None:
        _LOGGER.info("received signal %d, shutting down", signum)
        stop_event.set()

    for signame in ("SIGTERM", "SIGINT", "SIGBREAK"):
        signum = getattr(signal, signame, None)
        if signum is not None:
            try:
                signal.signal(signum, _handle_signal)
            except (ValueError, OSError):  # pragma: no cover - not the main thread
                pass

    pid_path = _write_pid_file(args.component)
    runner = run_dns if args.component == "dns" else run_proxy
    try:
        return runner(config, stop_event)
    finally:
        _remove_pid_file(pid_path)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
