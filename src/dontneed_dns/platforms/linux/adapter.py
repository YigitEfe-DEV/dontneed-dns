"""The Linux edition: systemd based service management.

Everything the Linux edition does goes through ``systemctl`` and
``journalctl``.  User scope (``systemctl --user``) is the default because it
needs no privileges at all; system scope is available for the cases where a
component has to run for every user on the machine and is only ever used
after the user confirmed the elevation.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

import dontneed_dns
from dontneed_dns.core.capability import LINUX_MATRIX, CapabilityMatrix
from dontneed_dns.core.constants import (
    DNS_UNIT_NAME,
    PROXY_UNIT_NAME,
)
from dontneed_dns.core.system import CommandResult, describe_failure, is_root, run_command, which
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

#: Name of the executable that the installer places in the user bin directory.
INSTALLED_BINARY_NAME = "dontneed-dns"

#: The unit of the transparent router.  It is a system unit, because the
#: router has to change the nftables rules of the machine.
TRANSPARENT_UNIT_NAME = "dontneed-dns-transparent.service"


def current_user_name() -> str:
    """The name of the user that runs the installer."""
    try:
        import pwd

        return pwd.getpwuid(os.getuid()).pw_name
    except (ImportError, KeyError):
        return os.environ.get("USER", "root")


def _systemd_quote(value: str) -> str:
    """Quote a value for a systemd command line.

    Paths with spaces or quotes have to be quoted, otherwise systemd splits
    the command at the wrong place.
    """
    if not value:
        return '""'
    if any(character.isspace() for character in value) or '"' in value or "\\" in value:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


_UNIT_FILES = {
    Component.DNS: DNS_UNIT_NAME,
    Component.PROXY: PROXY_UNIT_NAME,
}

_DESCRIPTIONS = {
    Component.DNS: "dontneed-dns DNS-over-HTTPS split resolver",
    Component.PROXY: "dontneed-dns TLS fragmenting SOCKS5 proxy",
}

_TRANSPARENT_UNIT_TEMPLATE = """[Unit]
Description=dontneed-dns transparent router
Documentation=https://github.com/YigitEfe-DEV/dontneed-dns
After=network-online.target systemd-resolved.service
Wants=network-online.target

[Service]
Type=simple
ExecStart={exec_start}
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1
User={user}
AmbientCapabilities=CAP_NET_ADMIN
CapabilityBoundingSet=CAP_NET_ADMIN
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths={state_dir}
ExecStopPost=-{nft} delete table ip {table}
ExecStopPost=-{nft} delete table ip6 {table}6

[Install]
WantedBy=multi-user.target
"""

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

    def __init__(
        self,
        scope: str = "user",
        *,
        executable: str | None = None,
        config_path: str | None = None,
    ) -> None:
        if scope not in ("user", "system"):
            raise ValueError(f"unknown scope {scope!r}")
        self.scope = scope
        #: The configuration file of the user scope.  ``None`` means the
        #: default location below the home directory.
        self.config_path = config_path
        #: The program the unit starts.  ``None`` means "the interpreter that
        #: runs this code".  The installer passes the path of the executable
        #: it placed in the user bin directory.
        self.executable = executable
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

    def running_pid(self, component: Component) -> int | None:
        """The process id of a running component, if there is one."""
        from dontneed_dns.core.paths import pid_file

        path = pid_file(f"dontneed-dns-{component.value}")
        try:
            pid = int(path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None
        from dontneed_dns.core.system import process_alive

        return pid if process_alive(pid) else None

    def _unit_name(self, component: Component) -> str:
        return _UNIT_FILES[component]

    def unit_name(self, component: Component) -> str:
        """The name of the unit file of ``component``."""
        return _UNIT_FILES[component]

    def unit_path(self, component: Component, scope: str = "user") -> Path:
        """The path of the unit file of ``component`` in ``scope``."""
        directory = SYSTEMD_USER_UNIT_DIR if scope == "user" else SYSTEMD_SYSTEM_UNIT_DIR
        return directory / _UNIT_FILES[component]

    def _unit_path(self, component: Component, scope: str) -> Path:
        return self.unit_path(component, scope)

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
            # A process can run without a unit, for example when it was
            # started by hand.  That is not an installation.
            if self.running_pid(component) is not None:
                return ServiceState.RUNNING_UNREGISTERED
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
        return OperationResult.failure(f"{unit}: {action} failed.", detail=describe_failure(result))

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
        return OperationResult.failure(
            f"{unit}: the logs could not be read.", detail=describe_failure(result)
        )

    # -- installation -----------------------------------------------------

    def _exec_start(self) -> tuple[str, str]:
        """Return the ``ExecStart`` program and the extra environment lines."""
        program = self.executable or sys.executable
        # A self contained build has no module tree and no interpreter to
        # point at: the installed executable runs the component itself.  An
        # explicit executable means the same thing, even when the installer
        # itself was not built as a frozen application.
        if getattr(sys, "frozen", False) or self.executable is not None:
            return f"{_systemd_quote(program)} run", ""
        module_root = Path(dontneed_dns.__file__).resolve().parent.parent
        python = program
        # A virtual environment already knows the package; a system wide
        # installation needs the module root on the import path.
        environment = "" if sys.prefix != sys.base_prefix else f"Environment=PYTHONPATH={module_root}"
        return f"{_systemd_quote(python)} -m dontneed_dns.service_runner", environment

    def _config_path(self, scope: str) -> Path:
        if scope == "user":
            if self.config_path:
                return Path(self.config_path)
            from dontneed_dns.core.paths import config_path

            return config_path()
        return SYSTEM_CONFIG_DIR / "config.toml"

    def _render_unit(self, component: Component, scope: str) -> str:
        exec_start, environment = self._exec_start()
        command = f"{exec_start} {component.value} --config {_systemd_quote(str(self._config_path(scope)))}"
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
                "systemd could not reload the unit files.", detail=describe_failure(reload_result)
            )

        enable_result = self._systemctl("enable", self._unit_name(component), scope=scope)
        if not enable_result.ok:
            return OperationResult.failure(
                f"{self._unit_name(component)} could not be enabled.",
                detail=describe_failure(enable_result),
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

        # A process that runs without a unit belongs to this project as
        # well and has to be stopped, otherwise it would keep the port.
        pid = self.running_pid(component)
        if pid is not None:
            import signal

            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
            changed = True

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

    # -- the transparent router -------------------------------------------

    def transparent_unit_path(self) -> Path:
        """The path of the unit file of the transparent router."""
        return SYSTEMD_SYSTEM_UNIT_DIR / TRANSPARENT_UNIT_NAME

    def _render_transparent_unit(self, user: str) -> str:
        from dontneed_dns.core.paths import data_dir

        program = self.executable or sys.executable
        config = self._config_path("user")
        return _TRANSPARENT_UNIT_TEMPLATE.format(
            exec_start=(f"{_systemd_quote(program)} run transparent --config {_systemd_quote(str(config))}"),
            user=user,
            state_dir=data_dir(),
            nft=_systemd_quote(shutil.which("nft") or "/usr/sbin/nft"),
            table=self.transparent_table(),
        )

    def transparent_table(self) -> str:
        """The name of the nftables tables of the project."""
        from dontneed_dns.core.config import default_config

        return default_config().transparent.nft_table

    @staticmethod
    def _elevated(argv: list[str], timeout: float = 60.0) -> CommandResult:
        """Run a command that needs administrator rights.

        The rights are taken from ``sudo`` when the process is not root.
        The credential is expected to be cached already, so that the
        password is asked only once.
        """
        if is_root():
            return run_command(argv, timeout=timeout)
        return run_command(["sudo", "-n", *argv], timeout=timeout)

    def install_transparent(self, *, user: str | None = None) -> OperationResult:
        """Install the unit of the transparent router.

        The unit is a system unit: the router has to change the nftables
        rules of the machine, which needs administrator rights.  The unit
        itself runs as the given user with the single capability it needs.
        """
        account = user or current_user_name()
        unit_path = self.transparent_unit_path()
        content = self._render_transparent_unit(account)

        from dontneed_dns.core.paths import data_dir

        staged = data_dir() / f"{TRANSPARENT_UNIT_NAME}.new"
        try:
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_text(content, encoding="utf-8")
        except OSError as exc:
            return OperationResult.failure(
                "the unit file could not be prepared.", detail=exc.strerror or str(exc)
            )
        existing = unit_path.read_text(encoding="utf-8") if unit_path.exists() else None
        changed = existing != content
        try:
            if changed:
                result = self._elevated(["install", "-m", "0644", str(staged), str(unit_path)])
                if not result.ok:
                    return OperationResult.needs_elevation(
                        "The transparent router could not be installed.",
                        detail=describe_failure(result),
                    )
        finally:
            try:
                staged.unlink()
            except OSError:
                pass

        for arguments in (["daemon-reload"], ["enable", TRANSPARENT_UNIT_NAME]):
            result = self._elevated(["systemctl", *arguments])
            if not result.ok:
                return OperationResult.failure(
                    f"systemctl {arguments[0]} failed.", detail=describe_failure(result), changed=changed
                )
        return OperationResult.success(f"{TRANSPARENT_UNIT_NAME} installed and enabled.", changed=changed)

    def uninstall_transparent(self) -> OperationResult:
        """Remove the unit of the transparent router and the rules it owns."""
        changed = False
        self._elevated(["systemctl", "disable", "--now", TRANSPARENT_UNIT_NAME])
        unit_path = self.transparent_unit_path()
        if unit_path.exists():
            result = self._elevated(["rm", "-f", str(unit_path)])
            if not result.ok:
                return OperationResult.failure(
                    f"the unit file {unit_path} could not be removed.",
                    detail=describe_failure(result),
                )
            changed = True
        self._elevated(["systemctl", "daemon-reload"])
        # The rules of the project are removed as well, in case the unit
        # was killed without running its stop path.
        for family, name in (("ip", self.transparent_table()), ("ip6", self.transparent_table() + "6")):
            self._elevated(["nft", "delete", "table", family, name], timeout=30.0)
        return OperationResult.success(f"{TRANSPARENT_UNIT_NAME} removed.", changed=changed)

    def transparent_status(self) -> ServiceState:
        """The state of the transparent router."""
        if not is_root() and not self.is_available():
            return ServiceState.UNKNOWN
        result = run_command(["systemctl", "is-active", TRANSPARENT_UNIT_NAME], timeout=30.0)
        state = result.stdout.strip().lower()
        if state == "active":
            return ServiceState.RUNNING
        if state == "failed":
            return ServiceState.FAILED
        if state == "inactive":
            if self.transparent_unit_path().exists():
                return ServiceState.STOPPED
            return ServiceState.NOT_INSTALLED
        return ServiceState.UNKNOWN

    def install_paths(self, scope: str = "user") -> dict[str, Path]:
        """Return the paths this project writes to."""
        paths = {
            "config": self._config_path(scope),
            "dns_unit": self._unit_path(Component.DNS, scope),
            "proxy_unit": self._unit_path(Component.PROXY, scope),
            "binary": Path(self.executable) if self.executable else Path(sys.executable),
        }
        if scope == "system":
            paths["transparent_unit"] = self.transparent_unit_path()
        if scope == "user":
            from dontneed_dns.core.paths import data_dir, log_dir

            paths["data"] = data_dir()
            paths["logs"] = log_dir()
        else:
            paths["data"] = SYSTEM_CONFIG_DIR
            paths["logs"] = Path("/var/log")
        return paths
