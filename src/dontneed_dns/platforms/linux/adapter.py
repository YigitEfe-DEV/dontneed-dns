"""The Linux edition: systemd based service management.

Everything the Linux edition does goes through ``systemctl`` and
``journalctl``.  User scope (``systemctl --user``) is the default because it
needs no privileges at all; system scope is available for the cases where a
component has to run for every user on the machine and is only ever used
after the user confirmed the elevation.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import dontneed_dns
from dontneed_dns.core.capability import LINUX_MATRIX, CapabilityMatrix
from dontneed_dns.core.constants import (
    DNS_UNIT_NAME,
    PROXY_UNIT_NAME,
)
from dontneed_dns.core.system import CommandResult, is_root, run_command, which
from dontneed_dns.platforms.base import (
    Component,
    OperationResult,
    PlatformAdapter,
    ServiceState,
)
from dontneed_dns.platforms.linux.distro import DistroInfo, detect_distro

__all__ = ["SYSTEMD_SYSTEM_UNIT_DIR", "SYSTEMD_USER_UNIT_DIR", "LinuxPlatformAdapter"]


def _user_unit_dir() -> Path:
    """The directory systemd reads the user units from.

    systemd honours ``XDG_CONFIG_HOME``, so the unit has to be written
    there and not always into ``~/.config``.
    """
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "systemd" / "user"


SYSTEMD_USER_UNIT_DIR = _user_unit_dir()
SYSTEMD_SYSTEM_UNIT_DIR = Path("/etc/systemd/system")
SYSTEM_CONFIG_DIR = Path("/etc/dontneed-dns")

_UNIT_FILES = {
    Component.DNS: DNS_UNIT_NAME,
    Component.PROXY: PROXY_UNIT_NAME,
}

_DESCRIPTIONS = {
    Component.DNS: "dontneed-dns DNS-over-HTTPS split resolver",
    Component.PROXY: "dontneed-dns TLS fragmenting SOCKS5 proxy",
}

_UNIT_TEMPLATE = """[Unit]
Description={description}
Documentation=https://github.com/YigitEfe-DEV/dontneed-dns
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={exec_start}
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1
{environment}
{hardening}
[Install]
WantedBy={wanted_by}
"""

_USER_HARDENING = """NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=%s
"""

_SYSTEM_HARDENING = """NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
"""


class LinuxPlatformAdapter(PlatformAdapter):
    """Manage the project components with systemd."""

    name = "linux"

    def __init__(self, scope: str = "user") -> None:
        if scope not in ("user", "system"):
            raise ValueError(f"unknown scope {scope!r}")
        self.scope = scope
        self.distro: DistroInfo = detect_distro()

    # -- introspection ----------------------------------------------------

    def is_available(self) -> bool:
        """``True`` when systemd is the init system."""
        if which("systemctl") is None:
            return False
        return Path("/run/systemd/system").exists()

    def describe(self) -> str:
        """A short description of the host."""
        systemd = "systemd" if self.is_available() else "no systemd"
        return f"{self.distro.name} ({self.distro.identifier}), {systemd}"

    def capabilities(self) -> CapabilityMatrix:
        """The Linux capability matrix."""
        return LINUX_MATRIX

    def diagnostics(self) -> dict[str, Any]:
        """Collect the state of the installation."""
        data: dict[str, Any] = {
            "platform": "linux",
            "distribution": self.distro.name,
            "distribution_id": self.distro.identifier,
            "distribution_family": self.distro.family,
            "distribution_known": self.distro.is_known,
            "systemd_available": self.is_available(),
            "scope": self.scope,
            "components": {},
        }
        for component in Component:
            data["components"][component.value] = {
                "unit": _UNIT_FILES[component],
                "state": self.status(component).value,
                "enabled": self.is_enabled(component),
                "unit_file": str(self._unit_path(component, self.scope)),
                "unit_file_exists": self._unit_path(component, self.scope).exists(),
            }
        return data

    # -- helpers ----------------------------------------------------------

    def _unit_name(self, component: Component) -> str:
        return _UNIT_FILES[component]

    def _unit_path(self, component: Component, scope: str) -> Path:
        directory = SYSTEMD_USER_UNIT_DIR if scope == "user" else SYSTEMD_SYSTEM_UNIT_DIR
        return directory / _UNIT_FILES[component]

    def _systemctl(self, *args: str, scope: str | None = None, timeout: float = 30.0) -> CommandResult:
        argv = ["systemctl"]
        if (scope or self.scope) == "user":
            argv.append("--user")
        argv.extend(args)
        return run_command(argv, timeout=timeout)

    def _require_root(self, action: str) -> OperationResult | None:
        if is_root():
            return None
        return OperationResult.needs_elevation(
            f"{action} needs root privileges on this system.",
            detail="Re-run the command with sudo, or use the user scope instead.",
        )

    # -- lifecycle --------------------------------------------------------

    def status(self, component: Component) -> ServiceState:
        """Return the state of ``component``."""
        if not self.is_available():
            return ServiceState.UNKNOWN
        unit = self._unit_name(component)

        load_state = self._systemctl("show", "--property=LoadState", "--value", unit)
        if load_state.ok and load_state.stdout.strip() == "not-found":
            return ServiceState.NOT_INSTALLED

        result = self._systemctl("is-active", unit)
        state = result.stdout.strip().lower()
        if state == "active":
            return ServiceState.RUNNING
        if state == "inactive":
            return ServiceState.STOPPED
        if state == "failed":
            return ServiceState.FAILED
        if state in ("activating", "deactivating", "reloading"):
            return ServiceState.UNKNOWN
        if not load_state.ok and load_state.stdout.strip() == "":
            return ServiceState.NOT_INSTALLED
        return ServiceState.UNKNOWN

    def is_enabled(self, component: Component) -> bool | None:
        """Return whether the unit starts automatically."""
        if not self.is_available():
            return None
        result = self._systemctl("is-enabled", self._unit_name(component))
        # ``systemctl is-enabled`` exits with a non-zero status for a unit
        # that is simply disabled, so the output decides, not the status.
        state = result.stdout.strip().lower()
        if state in ("enabled", "enabled-runtime", "static", "linked"):
            return True
        if state in ("disabled", "masked", "masked-runtime"):
            return False
        return None

    def _lifecycle(self, action: str, component: Component) -> OperationResult:
        if not self.is_available():
            return OperationResult.not_supported("systemd is not available on this system.")
        unit = self._unit_name(component)
        result = self._systemctl(action, unit, timeout=60.0)
        if result.ok:
            return OperationResult.success(f"{unit}: {action} succeeded.", changed=True)
        return OperationResult.failure(f"{unit}: {action} failed.", detail=result.summary())

    def start(self, component: Component) -> OperationResult:
        """Start ``component``."""
        return self._lifecycle("start", component)

    def stop(self, component: Component) -> OperationResult:
        """Stop ``component``."""
        return self._lifecycle("stop", component)

    def restart(self, component: Component) -> OperationResult:
        """Restart ``component``."""
        return self._lifecycle("restart", component)

    def enable(self, component: Component) -> OperationResult:
        """Enable automatic startup of ``component``."""
        return self._lifecycle("enable", component)

    def disable(self, component: Component) -> OperationResult:
        """Disable automatic startup of ``component``."""
        return self._lifecycle("disable", component)

    def logs(self, component: Component, lines: int = 50) -> OperationResult:
        """Return the recent journal entries of ``component``."""
        if which("journalctl") is None:
            return OperationResult.failure("journalctl is not available on this system.")
        unit = self._unit_name(component)
        argv = ["journalctl"]
        if self.scope == "user":
            argv.append("--user")
        argv.extend(["-u", unit, "-n", str(max(1, lines)), "--no-pager", "--output=short-iso"])
        result = run_command(argv, timeout=30.0)
        if result.ok:
            return OperationResult.success(f"{unit}: {lines} log lines.", data={"output": result.stdout})
        return OperationResult.failure(f"{unit}: the logs could not be read.", detail=result.summary())

    # -- installation -----------------------------------------------------

    def _exec_start(self) -> tuple[str, str]:
        module_root = Path(dontneed_dns.__file__).resolve().parent.parent
        python = sys.executable
        # A virtual environment already knows the package; a system wide
        # installation needs the module root on the import path.
        environment = "" if sys.prefix != sys.base_prefix else f"Environment=PYTHONPATH={module_root}"
        return f"{python} -m dontneed_dns.service_runner", environment

    def _config_path(self, scope: str) -> Path:
        if scope == "user":
            from dontneed_dns.core.paths import config_path

            return config_path()
        return SYSTEM_CONFIG_DIR / "config.toml"

    def _render_unit(self, component: Component, scope: str) -> str:
        exec_start, environment = self._exec_start()
        command = f"{exec_start} {component.value} --config {self._config_path(scope)}"
        if scope == "user":
            from dontneed_dns.core.paths import data_dir

            hardening = _USER_HARDENING % data_dir()
            wanted_by = "default.target"
        else:
            hardening = _SYSTEM_HARDENING
            wanted_by = "multi-user.target"
        return _UNIT_TEMPLATE.format(
            description=_DESCRIPTIONS[component],
            exec_start=command,
            environment=environment,
            hardening=hardening,
            wanted_by=wanted_by,
        )

    def install(self, component: Component, *, scope: str = "user") -> OperationResult:
        """Install the systemd unit of ``component``. Idempotent."""
        if not self.is_available():
            return OperationResult.not_supported("systemd is not available on this system.")
        if scope == "system":
            denied = self._require_root("Installing a system service")
            if denied is not None:
                return denied

        if scope == "user":
            # The unit may only write into this directory; systemd refuses to
            # start the unit when the path does not exist.
            from dontneed_dns.core.paths import data_dir

            try:
                data_dir().mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                return OperationResult.failure(
                    f"the state directory {data_dir()} could not be created.",
                    detail=exc.strerror or str(exc),
                )

        unit_path = self._unit_path(component, scope)
        content = self._render_unit(component, scope)
        changed = False
        try:
            unit_path.parent.mkdir(parents=True, exist_ok=True)
            existing = unit_path.read_text(encoding="utf-8") if unit_path.exists() else None
            if existing != content:
                unit_path.write_text(content, encoding="utf-8")
                changed = True
        except OSError as exc:
            return OperationResult.failure(
                f"the unit file {unit_path} could not be written.", detail=exc.strerror or str(exc)
            )

        if scope == "system":
            config_path = self._config_path(scope)
            try:
                if not config_path.exists():
                    config_path.parent.mkdir(parents=True, exist_ok=True)
                    from dontneed_dns.core.config import config_to_toml, default_config

                    config_path.write_text(config_to_toml(default_config()), encoding="utf-8")
                    config_path.chmod(0o644)
                    changed = True
            except OSError as exc:
                return OperationResult.failure(
                    f"the configuration {config_path} could not be written.",
                    detail=exc.strerror or str(exc),
                )

        reload_result = self._systemctl("daemon-reload", scope=scope)
        if not reload_result.ok:
            return OperationResult.failure(
                "systemd could not reload the unit files.", detail=reload_result.summary()
            )

        enable_result = self._systemctl("enable", self._unit_name(component), scope=scope)
        if not enable_result.ok:
            return OperationResult.failure(
                f"{self._unit_name(component)} could not be enabled.",
                detail=enable_result.summary(),
                changed=changed,
            )

        return OperationResult.success(
            f"{self._unit_name(component)} installed in {scope} scope.", changed=changed
        )

    def uninstall(self, component: Component, *, scope: str = "user") -> OperationResult:
        """Remove the systemd unit of ``component`` and nothing else."""
        if not self.is_available():
            return OperationResult.not_supported("systemd is not available on this system.")
        if scope == "system":
            denied = self._require_root("Removing a system service")
            if denied is not None:
                return denied

        unit = self._unit_name(component)
        changed = False

        self._systemctl("disable", "--now", unit, scope=scope)
        self._systemctl("stop", unit, scope=scope)

        unit_path = self._unit_path(component, scope)
        if unit_path.exists():
            try:
                unit_path.unlink()
                changed = True
            except OSError as exc:
                return OperationResult.failure(
                    f"the unit file {unit_path} could not be removed.", detail=exc.strerror or str(exc)
                )

        if scope == "system":
            config_path = self._config_path(scope)
            if config_path.exists():
                try:
                    config_path.unlink()
                    changed = True
                except OSError as exc:
                    return OperationResult.failure(
                        f"the configuration {config_path} could not be removed.",
                        detail=exc.strerror or str(exc),
                    )

        self._systemctl("daemon-reload", scope=scope)
        return OperationResult.success(f"{unit} removed from {scope} scope.", changed=changed)

    def install_paths(self, scope: str = "user") -> dict[str, Path]:
        """Return the paths this project writes to."""
        paths = {
            "config": self._config_path(scope),
            "dns_unit": self._unit_path(Component.DNS, scope),
            "proxy_unit": self._unit_path(Component.PROXY, scope),
        }
        if scope == "user":
            from dontneed_dns.core.paths import data_dir, log_dir

            paths["data"] = data_dir()
            paths["logs"] = log_dir()
        else:
            paths["data"] = SYSTEM_CONFIG_DIR
            paths["logs"] = Path("/var/log")
        return paths
