"""The self contained installer of the Fedora edition.

The release artifact is a single executable that carries the application
and its runtime.  Running it installs the application into the user data
directory, registers the systemd user services through the existing
:class:`~dontneed_dns.platforms.linux.adapter.LinuxPlatformAdapter`, starts
them and reports the result.

The installer never needs root privileges: everything it writes stays below
the home directory of the user, and the services run as that user.  Nothing
outside of the paths this project owns is touched.
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from dontneed_dns import __version__
from dontneed_dns.core.config import Config, default_config, load_config, save_config
from dontneed_dns.core.errors import ConfigError
from dontneed_dns.core.health import Severity, run_health_checks
from dontneed_dns.core.paths import bin_dir, config_path, data_dir, install_dir, log_dir
from dontneed_dns.core.system import is_linux, is_root
from dontneed_dns.platforms.base import Component, OperationResult
from dontneed_dns.platforms.linux.adapter import INSTALLED_BINARY_NAME, LinuxPlatformAdapter
from dontneed_dns.platforms.linux.distro import detect_distro

__all__ = [
    "INSTALLER_VERSION",
    "Installer",
    "Step",
    "main",
]

#: The version of the installer itself.  It equals the version of the
#: application that is bundled into the executable.
INSTALLER_VERSION = __version__

#: The architectures the release artifact is built for.
SUPPORTED_ARCHITECTURES = ("x86_64", "amd64")

#: Free space that has to be available on top of the payload.
DISK_SPACE_MARGIN = 20 * 1024 * 1024

#: How long the installer waits for the listeners to appear.
STARTUP_WAIT_SECONDS = 3.0


class InstallerError(Exception):
    """A condition that stops the installation."""


@dataclass
class Step:
    """The outcome of a single installation step."""

    name: str
    ok: bool = True
    detail: str = ""
    warning: bool = False
    fatal: bool = False

    @property
    def marker(self) -> str:
        """A short marker for the report."""
        if not self.ok:
            return "FAIL"
        if self.warning:
            return "WARN"
        return "OK"


@dataclass
class Installer:
    """Installs, inspects and removes the Fedora edition."""

    config_path: Path | None = None
    scope: str = "user"
    payload_source: Path | None = None
    target_binary: Path | None = None
    adapter: LinuxPlatformAdapter | None = None
    #: The health check runner.  The default runs the real checks; the
    #: tests inject a stub.
    health_runner: Callable[[Config], list] | None = None
    #: How long the installer waits for the listeners to appear.
    startup_wait: float = STARTUP_WAIT_SECONDS
    #: ``True`` when this process is the self contained executable.  Only
    #: then is a payload copied into the installation directory.
    frozen: bool = field(default_factory=lambda: getattr(sys, "frozen", False))
    #: Overrides ``service.autostart`` from the configuration file.
    autostart_override: bool | None = None
    steps: list[Step] = field(default_factory=list)

    # -- helpers ----------------------------------------------------------

    def _record(self, name: str, ok: bool, detail: str = "", *, warning: bool = False) -> Step:
        step = Step(name=name, ok=ok, detail=detail, warning=warning)
        self.steps.append(step)
        return step

    def _fail(self, name: str, detail: str) -> Step:
        step = Step(name=name, ok=False, detail=detail, fatal=True)
        self.steps.append(step)
        return step

    @property
    def target_config(self) -> Path:
        """The configuration file the installation uses."""
        return self.config_path or config_path()

    @property
    def target_binary_path(self) -> Path:
        """The path the executable is installed to."""
        return self.target_binary or (bin_dir() / INSTALLED_BINARY_NAME)

    @property
    def source_binary(self) -> Path:
        """The file that is copied into the installation directory."""
        return self.payload_source or Path(sys.executable)

    def _make_adapter(self) -> LinuxPlatformAdapter:
        if self.adapter is not None:
            return self.adapter
        executable = str(self.target_binary_path) if self.frozen else None
        self.adapter = LinuxPlatformAdapter(
            scope=self.scope,
            executable=executable,
            config_path=str(self.target_config),
        )
        return self.adapter

    # -- preflight --------------------------------------------------------

    def check_platform(self) -> Step:
        """Check that the host is a supported Linux system."""
        if not is_linux():
            return self._fail("Platform", "this installer only runs on Linux")
        info = detect_distro()
        if info.identifier == "fedora":
            return self._record("Platform", True, f"Fedora {info.version} ({info.identifier})")
        if info.is_known:
            return self._record(
                "Platform",
                True,
                f"{info.name} ({info.identifier}) is not Fedora; continuing anyway",
                warning=True,
            )
        return self._record(
            "Platform",
            True,
            f"{info.name} ({info.identifier}) is not a tested distribution; continuing anyway",
            warning=True,
        )

    def check_architecture(self) -> Step:
        """Check that the executable matches the architecture of the host."""
        machine = platform.machine().lower()
        if machine not in SUPPORTED_ARCHITECTURES:
            return self._fail(
                "Architecture",
                f"this executable is built for x86_64, this machine is {machine}",
            )
        return self._record("Architecture", True, f"{machine} (supported)")

    def check_system(self) -> Step:
        """Check that the host can run the services."""
        adapter = self._make_adapter()
        if not adapter.is_available():
            return self._fail("systemd", "systemd is not available on this system")
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
        if not runtime_dir or not Path(runtime_dir).is_dir():
            return self._fail(
                "User session",
                "no user session was found; run the installer from a terminal inside your login session",
            )
        detail = f"systemd is available, user session {runtime_dir}"
        if is_root():
            detail += ", running as root (the services will still run as this user)"
        return self._record("systemd", True, detail)

    def check_disk_space(self) -> Step:
        """Check that there is enough free space for the installation."""
        target = self.target_binary_path
        try:
            source_size = self.source_binary.stat().st_size
        except OSError:
            source_size = 0
        required = source_size * 2 + DISK_SPACE_MARGIN
        parent = target.parent
        probe = parent if parent.exists() else parent.parent
        try:
            free = shutil.disk_usage(probe).free
        except OSError as exc:
            return self._record("Disk space", True, f"could not be determined ({exc.strerror})", warning=True)
        if free < required:
            return self._fail(
                "Disk space",
                f"{free // (1024 * 1024)} MiB free at {probe}, {required // (1024 * 1024)} MiB are needed",
            )
        return self._record(
            "Disk space",
            True,
            f"{free // (1024 * 1024)} MiB free at {probe}, {required // (1024 * 1024)} MiB needed",
        )

    def check_existing(self) -> Step:
        """Report what is already installed."""
        adapter = self._make_adapter()
        found = []
        if install_dir().exists():
            found.append(f"application directory {install_dir()}")
        if self.target_config.exists():
            found.append(f"configuration {self.target_config}")
        for component in Component:
            if adapter.unit_path(component, self.scope).exists():
                found.append(f"unit {adapter.unit_name(component)}")
        if not found:
            return self._record("Existing installation", True, "none found, this is a fresh installation")
        return self._record(
            "Existing installation",
            True,
            "found " + ", ".join(found) + "; the configuration is preserved",
        )

    def preflight(self) -> list[Step]:
        """Run every check that does not change anything."""
        self.steps.clear()
        self.check_platform()
        self.check_architecture()
        self.check_system()
        self.check_disk_space()
        self.check_existing()
        return list(self.steps)

    # -- installation -----------------------------------------------------

    def _write_payload(self) -> Step:
        """Copy the executable into the installation directory."""
        source = self.source_binary
        target = self.target_binary_path
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(target.parent, 0o755)
            if source.resolve() != target.resolve():
                temporary = target.with_suffix(target.suffix + ".new")
                shutil.copy2(source, temporary)
                os.chmod(temporary, 0o755)
                os.replace(temporary, target)
            os.chmod(target, 0o755)
        except OSError as exc:
            return self._fail("Application", f"could not be installed: {exc.strerror or exc}")
        return self._record("Application", True, f"installed to {target}")

    def _prepare_config(self) -> tuple[Config, Step]:
        """Load the configuration, creating it when it does not exist."""
        target = self.target_config
        if target.exists():
            self._restrict_permissions(target)
            try:
                config = load_config(target)
            except ConfigError as exc:
                config = default_config()
                config.path = target
                return config, self._record(
                    "Configuration",
                    False,
                    f"the existing configuration is invalid ({exc}); the defaults are used",
                    warning=True,
                )
            return config, self._record("Configuration", True, f"preserved at {target}")
        config = default_config()
        config.path = target
        try:
            save_config(config, target)
            os.chmod(target, 0o600)
        except OSError as exc:
            return config, self._fail("Configuration", f"could not be written: {exc.strerror or exc}")
        return config, self._record("Configuration", True, f"created at {target}")

    @staticmethod
    def _restrict_permissions(path: Path) -> None:
        """Make sure the configuration cannot be changed by other users.

        The file belongs to this project, so the installer may tighten its
        permissions.  A file that another user can write would let that
        user change the routing of the machine.
        """
        try:
            mode = path.stat().st_mode & 0o777
        except OSError:
            return
        if mode & 0o077:
            try:
                path.chmod(0o600)
            except OSError:
                pass

    def _register(self, adapter: LinuxPlatformAdapter, config: Config) -> Step:
        """Register the systemd user services."""
        failures = []
        for component in Component:
            result: OperationResult = adapter.install(component, scope=self.scope)
            if not result.ok:
                failures.append(f"{component.label}: {result.message}")
        if failures:
            return self._fail("Services", "; ".join(failures))
        return self._record(
            "Services",
            True,
            "registered " + ", ".join(adapter.unit_name(component) for component in Component),
        )

    def _apply_autostart(self, adapter: LinuxPlatformAdapter, config: Config) -> Step:
        """Enable or disable the automatic start according to the configuration."""
        autostart = config.service.autostart if self.autostart_override is None else self.autostart_override
        if autostart:
            failures = []
            for component in Component:
                result = adapter.enable(component)
                if not result.ok:
                    failures.append(f"{component.label}: {result.message}")
            if failures:
                return self._record("Automatic start", False, "; ".join(failures), warning=True)
            return self._record(
                "Automatic start",
                True,
                "enabled: the services start after every login",
            )
        for component in Component:
            adapter.disable(component)
        return self._record(
            "Automatic start",
            True,
            "disabled by service.autostart in the configuration; start the services manually",
        )

    def _start(self, adapter: LinuxPlatformAdapter) -> Step:
        """Start both components."""
        failures = []
        for component in Component:
            result = adapter.start(component)
            if not result.ok:
                failures.append(f"{component.label}: {result.message}")
        if failures:
            return self._fail("Startup", "; ".join(failures))
        return self._record("Startup", True, "the DNS resolver and the proxy are running")

    def _health(self, config: Config) -> Step:
        """Run the health checks and summarise them."""
        runner = self.health_runner or (lambda item: run_health_checks(item, timeout=5.0))
        checks = runner(config)
        failed = [check for check in checks if not check.ok and check.severity != Severity.WARNING]
        warnings = [check for check in checks if not check.ok and check.severity == Severity.WARNING]
        if failed:
            names = ", ".join(check.name for check in failed)
            return self._record("Health", False, f"failed: {names}")
        if warnings:
            names = ", ".join(check.name for check in warnings)
            return self._record("Health", True, f"warnings: {names}", warning=True)
        return self._record("Health", True, "every check passed")

    def install(self) -> int:
        """Run the whole installation and return the exit code."""
        self.steps.clear()
        self.preflight()
        if any(step.fatal for step in self.steps):
            return 1

        adapter = self._make_adapter()
        if self.frozen:
            payload = self._write_payload()
            if not payload.ok:
                return 1
        else:
            self._record(
                "Application",
                True,
                "not a self contained build; the services run the current installation",
                warning=True,
            )
        config, _config_step = self._prepare_config()
        if not self._register(adapter, config).ok:
            return 1
        self._apply_autostart(adapter, config)
        if not self._start(adapter).ok:
            return 1
        if self.startup_wait > 0:
            time.sleep(self.startup_wait)
        self._health(config)
        return 0

    # -- removal ----------------------------------------------------------

    def uninstall(self, *, purge: bool = False) -> int:
        """Remove the installation and keep the configuration by default."""
        self.steps.clear()
        adapter = self._make_adapter()

        failures = []
        for component in Component:
            result = adapter.uninstall(component, scope=self.scope)
            if not result.ok:
                failures.append(f"{component.label}: {result.message}")
        if failures:
            self._record("Services", False, "; ".join(failures), warning=True)
        else:
            self._record("Services", True, "the systemd user services were removed")

        target = self.target_binary_path
        removed = []
        try:
            if target.exists():
                target.unlink()
                removed.append(str(target))
            bin_directory = bin_dir()
            if bin_directory.exists() and not any(bin_directory.iterdir()):
                bin_directory.rmdir()
                removed.append(str(bin_directory))
            root = install_dir()
            if root.exists() and not any(root.iterdir()):
                root.rmdir()
                removed.append(str(root))
        except OSError as exc:
            self._record("Application", False, f"could not be removed: {exc.strerror or exc}", warning=True)
        else:
            if removed:
                self._record("Application", True, "removed " + ", ".join(removed))
            else:
                self._record("Application", True, "nothing to remove")

        for name in ("dontneed-dns-dns.pid", "dontneed-dns-proxy.pid"):
            stale = data_dir() / name
            try:
                if stale.exists():
                    stale.unlink()
            except OSError:
                pass

        if purge:
            try:
                if self.target_config.exists():
                    self.target_config.unlink()
                    self._record("Configuration", True, f"removed {self.target_config}")
                else:
                    self._record("Configuration", True, "there was no configuration to remove")
            except OSError as exc:
                self._record(
                    "Configuration", False, f"could not be removed: {exc.strerror or exc}", warning=True
                )
        else:
            self._record("Configuration", True, f"kept at {self.target_config}")
        return 0

    # -- status -----------------------------------------------------------

    def status(self) -> int:
        """Report the state of the installation."""
        self.steps.clear()
        adapter = self._make_adapter()
        self._record("Version", True, f"dontneed-dns {__version__}")
        self._record("Executable", True, str(self.target_binary_path))
        self._record("Configuration", True, str(self.target_config))
        self._record("Logs", True, str(log_dir()))
        for component in Component:
            state = adapter.status(component)
            enabled = adapter.is_enabled(component)
            autostart = "unknown" if enabled is None else ("yes" if enabled else "no")
            self._record(
                component.label,
                state is not None,
                f"{state.label}, starts automatically: {autostart}",
            )
        return 0


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

HELP_TEXT = """\
dontneed-dns {version} - the Fedora edition installer

Usage:
  dontneed-dns-fedora-x86_64 [OPTION]        install the application
  dontneed-dns-fedora-x86_64 --status        show the state of the installation
  dontneed-dns-fedora-x86_64 --uninstall     remove the installation
  dontneed-dns-fedora-x86_64 --version       print the version
  dontneed-dns-fedora-x86_64 --help          print this text

Options:
  --install         run the installation (the default without arguments)
  --uninstall       stop and remove the services and the application
  --purge           also remove the configuration file (with --uninstall)
  --status          print the state of the installation
  --config PATH     use a different configuration file
  --autostart       start the services automatically after every login
  --no-autostart    do not start the services automatically after every login
  --yes             do not ask for a confirmation
  --version         print the version and exit
  --help            print this text and exit

Every other argument is passed to the application itself, for example:
  dontneed-dns-fedora-x86_64 status
  dontneed-dns-fedora-x86_64 tui
  dontneed-dns-fedora-x86_64 dns start

The installation writes only into your home directory:
  {install_dir}    the application
  {config_path}    the configuration
  {log_dir}        the log files
"""


def _print_steps(steps: list[Step]) -> None:
    for step in steps:
        marker = step.marker
        if marker == "OK":
            print(f"[ {marker} ] {step.name}: {step.detail}")
        else:
            print(f"[{marker:>4}] {step.name}: {step.detail}")


def main(argv: list[str] | None = None) -> int:
    """Entry point of the installer."""
    args = list(sys.argv[1:] if argv is None else argv)

    if "--help" in args or "-h" in args:
        print(
            HELP_TEXT.format(
                version=__version__,
                install_dir=install_dir(),
                config_path=config_path(),
                log_dir=log_dir(),
            )
        )
        return 0

    if "--version" in args or "-V" in args:
        print(f"dontneed-dns {__version__}")
        return 0

    config: Path | None = None
    purge = False
    if "--config" in args:
        index = args.index("--config")
        if index + 1 >= len(args):
            print("error: --config needs a path", file=sys.stderr)
            return 2
        config = Path(args[index + 1])
        del args[index : index + 2]
    if "--purge" in args:
        purge = True
        args.remove("--purge")
    autostart_override: bool | None = None
    if "--autostart" in args:
        autostart_override = True
        args.remove("--autostart")
    if "--no-autostart" in args:
        autostart_override = False
        args.remove("--no-autostart")
    for flag in ("--yes", "-y"):
        if flag in args:
            args.remove(flag)

    installer = Installer(config_path=config, autostart_override=autostart_override)

    if "--status" in args:
        args.remove("--status")
        installer.status()
        _print_steps(installer.steps)
        return 0

    if "--uninstall" in args:
        args.remove("--uninstall")
        installer.uninstall(purge=purge)
        _print_steps(installer.steps)
        return 0

    if "--install" in args:
        args.remove("--install")

    print("dontneed-dns installer")
    print()
    code = installer.install()
    _print_steps(installer.steps)
    print()
    if code == 0:
        print("The installation is complete.")
        print(f"  command    {installer.target_binary_path}")
        print(f"  interface  {installer.target_binary_path} tui")
        print(f"  status     {installer.target_binary_path} status")
        print(f"  logs       {installer.target_binary_path} logs -n 50")
    else:
        print("The installation did not finish. The output above names the step that failed.")
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
