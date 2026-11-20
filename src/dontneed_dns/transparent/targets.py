"""The addresses that are routed through the transparent router.

The router learns the addresses of the target domains from the resolver
itself: every answer that the split resolver gives for a target domain
is written to a small state file, and the router reads that file and adds
the addresses to the nftables set.  The router also resolves the target
domains on its own every few minutes, so that the set stays correct when
an application uses a cached address or its own resolver.

The file is written atomically, so that the router never reads a half
written document.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

from dontneed_dns.core.logging_setup import get_logger
from dontneed_dns.core.paths import data_dir

__all__ = [
    "TargetWatcher",
    "read_targets",
    "targets_path",
    "write_targets",
]

_LOGGER = get_logger("transparent.targets")

FILE_NAME = "transparent-targets.json"


def targets_path() -> Path:
    """The path of the file with the learned addresses."""
    return data_dir() / FILE_NAME


def write_targets(addresses: list[str] | tuple[str, ...], path: Path | None = None) -> None:
    """Write the learned addresses atomically."""
    target = path or targets_path()
    unique = sorted({address for address in addresses if address})
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=str(target.parent), prefix=".targets-", delete=False
        ) as handle:
            json.dump({"addresses": unique}, handle)
            handle.flush()
            os.fsync(handle.fileno())
            name = handle.name
        os.replace(name, target)
    except OSError as exc:
        _LOGGER.info("the learned addresses could not be written: %s", exc)


def read_targets(path: Path | None = None) -> list[str]:
    """Read the learned addresses."""
    target = path or targets_path()
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    addresses = document.get("addresses")
    if not isinstance(addresses, list):
        return []
    return [address for address in addresses if isinstance(address, str)]


class TargetWatcher:
    """Keeps the address set of the router up to date.

    Two sources are used: the file that the resolver writes and a direct
    DNS-over-HTTPS query of the target domains.
    """

    def __init__(
        self,
        manager,
        domains: tuple[str, ...],
        *,
        refresh_seconds: float = 300.0,
        poll_seconds: float = 2.0,
        resolver=None,
        path: Path | None = None,
        on_change: Callable[[list[str]], None] | None = None,
    ) -> None:
        self.manager = manager
        self.domains = tuple(domains)
        self.refresh_seconds = refresh_seconds
        self.poll_seconds = poll_seconds
        self.resolver = resolver
        self.path = path or targets_path()
        #: Called with the addresses of the set after every change.
        self.on_change = on_change
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_refresh = 0.0

    def start(self) -> None:
        """Start the watcher."""
        self._thread = threading.Thread(target=self._run, name="transparent-targets", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the watcher."""
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.update()
            except Exception as exc:  # noqa: BLE001 - the watcher must not die
                _LOGGER.info("the target set could not be updated: %s", exc)
            if self._stop.wait(self.poll_seconds):
                break

    def update(self) -> bool:
        """Read both sources and update the set.  Returns ``True`` on a change."""
        changed = self.manager.targets.add_many(read_targets(self.path))
        now = time.monotonic()
        if self.resolver is not None and self.domains and now - self._last_refresh >= self.refresh_seconds:
            self._last_refresh = now
            for domain in self.domains:
                try:
                    addresses = self.resolver.resolve_addresses(domain)
                except Exception as exc:  # noqa: BLE001 - one domain must not stop the rest
                    _LOGGER.info("the addresses of %s could not be resolved: %s", domain, exc)
                    continue
                changed = self.manager.targets.add_many(addresses) or changed
        if changed:
            try:
                self.manager.sync_targets()
            except Exception as exc:  # noqa: BLE001
                _LOGGER.info("the nftables set could not be updated: %s", exc)
            if self.on_change is not None:
                try:
                    self.on_change(self.manager.targets.as_json())
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.info("the routes could not be updated: %s", exc)
        return changed
