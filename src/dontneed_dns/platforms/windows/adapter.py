"""The Windows edition.

Two backends are available:

``scheduled-task`` (default)
    A Task Scheduler task that runs the component in the background of the
    current user.  No administrator rights are needed and the removal is
    complete.

``windows-service``
    A real Windows service registered with the Service Control Manager.
    It needs administrator rights and ``pywin32``; when either is missing
    the backend reports that it is unavailable instead of failing silently.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import dontneed_dns
from dontneed_dns.core.capability import WINDOWS_MATRIX, CapabilityMatrix
from dontneed_dns.core.constants import DNS_SERVICE_NAME, PROXY_SERVICE_NAME
from dontneed_dns.core.system import run_command
from dontneed_dns.platforms.base import (
    Component,
    OperationResult,
    PlatformAdapter,
    ServiceState,
)
from dontneed_dns.platforms.windows import paths as win_paths
from dontneed_dns.platforms.windows.scheduled_task import ScheduledTaskBackend, TaskInfo
from dontneed_dns.platforms.windows.scm import ServiceControlManagerBackend

__all__ = ["WindowsPlatformAdapter"]

_SERVICE_NAMES = {
    Component.DNS: DNS_SERVICE_NAME,
    Component.PROXY: PROXY_SERVICE_NAME,
}

_DISPLAY_NAMES = {
    Component.DNS: "dontneed-dns DNS resolver",
    Component.PROXY: "dontneed-dns proxy",
}


def _is_admin() -> bool:
    """``True`` when the process runs elevated."""
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001 - not Windows
        return False


class WindowsPlatformAdapter(PlatformAdapter):
    """Manage the project components on Windows."""

    name = "windows"

    def __init__(self, scope: str = "user", backend: str = "scheduled-task") -> None:
        if scope not in ("user", "system"):
            raise ValueError(f"unknown scope {scope!r}")
        if backend not in ("scheduled-task", "windows-service"):
            raise ValueError(f"unknown backend {backend!r}")
        self.scope = scope
        self.backend_name = backend
        self.tasks = ScheduledTaskBackend()
        self.scm = ServiceControlManagerBackend()
        for component, service_name in _SERVICE_NAMES.items():
            self.scm.register(component.value, service_name)

    # -- introspection ----------------------------------------------------

    def is_available(self) -> bool:
        """``True`` when this adapter can manage components on this machine."""
        return sys.platform.startswith("win") and self.tasks.is_available()

    def describe(self) -> str:
        """A short description of the host."""
        version = ""
        try:
            version = f"Windows {sys.getwindowsversion().major}.{sys.getwindowsversion().minor}"
        except Exception:  # noqa: BLE001 - defensive
            version = "Windows"
        return f"{version}, {self.backend_name} backend"

    def capabilities(self) -> CapabilityMatrix:
        """The Windows capability matrix."""
        return WINDOWS_MATRIX

    def diagnostics(self) -> dict[str, Any]:
        """Collect the state of the installation."""
        return {
            "platform": "windows",
            "os": self.describe(),
            "backend": self.backend_name,
            "elevated": _is_admin(),
            "scheduled_task_available": self.tasks.is_available(),
            "pywin32_available": self.scm.is_available(),
            "scope": self.scope,
            "components": {
                component.value: {
                    "task": self.tasks.task_name(component.value),
                    "state": self.status(component).value,
                    "enabled": self.is_enabled(component),
                }
                for component in Component
            },
            "paths": {key: str(value) for key, value in self.install_paths(self.scope).items()},
        }

    # -- helpers ----------------------------------------------------------

    def _pid_path(self, component: Component) -> Path:
        return win_paths.pid_file(f"dontneed-dns-{component.value}")

    def _task_name(self, component: Component) -> str:
        return self.tasks.task_name(component.value)

    def _command_line(self, component: Component) -> str:
        """The command the scheduled task or the service executes."""
        config = win_paths.config_path()
        if getattr(sys, "frozen", False):
            return f'"{sys.executable}" run {component.value} --config "{config}"'
        module_root = Path(dontneed_dns.__file__).resolve().parent.parent
        return (
            f'"{sys.executable}" -m dontneed_dns.service_runner {component.value} '
            f'--config "{config}" '
            f"&& set PYTHONPATH={module_root}"
        )

    def _info(self, component: Component) -> TaskInfo:
        return self.tasks.info(self._task_name(component), self._pid_path(component))

    # -- lifecycle --------------------------------------------------------

    def status(self, component: Component) -> ServiceState:
        """Return the state of ``component``."""
        if self.backend_name == "windows-service":
            info = self.scm.info(_SERVICE_NAMES[component])
            if not info.exists:
                return ServiceState.NOT_INSTALLED
            if info.running:
                return ServiceState.RUNNING
            return ServiceState.STOPPED

        info = self._info(component)
        if not info.exists:
            return ServiceState.NOT_INSTALLED
        if info.running:
            return ServiceState.RUNNING
        return ServiceState.STOPPED

    def is_enabled(self, component: Component) -> bool | None:
        """Return whether the component starts automatically."""
        if self.backend_name == "windows-service":
            info = self.scm.info(_SERVICE_NAMES[component])
            if not info.exists:
                return None
            return info.start_type.lower().startswith("auto")
        return self.tasks.is_enabled(self._task_name(component))

    def start(self, component: Component) -> OperationResult:
        """Start ``component``."""
        if self.backend_name == "windows-service":
            if not self.scm.is_available():
                return OperationResult.not_supported(
                    "the Windows service backend needs pywin32, which is not installed."
                )
            result = self.scm.start(_SERVICE_NAMES[component])
            if result.ok:
                return OperationResult.success(f"{_SERVICE_NAMES[component]} started.", changed=True)
            return OperationResult.failure(
                f"{_SERVICE_NAMES[component]} could not be started.", detail=result.summary()
            )

        name = self._task_name(component)
        if not self.tasks.exists(name):
            return OperationResult.failure(f"the task {name} is not installed.")
        result = self.tasks.run(name)
        if result.ok:
            return OperationResult.success(f"the task {name} was started.", changed=True)
        return OperationResult.failure(f"the task {name} could not be started.", detail=result.summary())

    def stop(self, component: Component) -> OperationResult:
        """Stop ``component``."""
        if self.backend_name == "windows-service":
            if not self.scm.is_available():
                return OperationResult.not_supported(
                    "the Windows service backend needs pywin32, which is not installed."
                )
            result = self.scm.stop(_SERVICE_NAMES[component])
            if result.ok:
                return OperationResult.success(f"{_SERVICE_NAMES[component]} stopped.", changed=True)
            return OperationResult.failure(
                f"{_SERVICE_NAMES[component]} could not be stopped.", detail=result.summary()
            )

        name = self._task_name(component)
        result = self.tasks.end(name)
        stopped = result.ok
        pid = self.tasks.read_pid(self._pid_path(component))
        if pid is not None:
            from dontneed_dns.core.system import process_alive

            if process_alive(pid):
                # The task did not stop the process; terminate it directly.
                run_command(["taskkill", "/PID", str(pid), "/T", "/F"], timeout=30)
                stopped = True
        try:
            self._pid_path(component).unlink()
        except OSError:
            pass
        if stopped:
            return OperationResult.success(f"the task {name} was stopped.", changed=True)
        return OperationResult.failure(f"the task {name} could not be stopped.", detail=result.summary())

    def restart(self, component: Component) -> OperationResult:
        """Restart ``component``."""
        self.stop(component)
        return self.start(component)

    def enable(self, component: Component) -> OperationResult:
        """Enable automatic startup of ``component``."""
        if self.backend_name == "windows-service":
            if not self.scm.is_available():
                return OperationResult.not_supported(
                    "the Windows service backend needs pywin32, which is not installed."
                )
            result = self.scm.set_start_type(_SERVICE_NAMES[component], "auto")
            if result.ok:
                return OperationResult.success(
                    f"{_SERVICE_NAMES[component]} now starts automatically.", changed=True
                )
            return OperationResult.failure(
                f"the start type of {_SERVICE_NAMES[component]} could not be changed.",
                detail=result.summary(),
            )

        name = self._task_name(component)
        result = self.tasks.enable(name)
        if result.ok:
            return OperationResult.success(f"the task {name} now starts automatically.", changed=True)
        return OperationResult.failure(f"the task {name} could not be enabled.", detail=result.summary())

    def disable(self, component: Component) -> OperationResult:
        """Disable automatic startup of ``component``."""
        if self.backend_name == "windows-service":
            if not self.scm.is_available():
                return OperationResult.not_supported(
                    "the Windows service backend needs pywin32, which is not installed."
                )
            result = self.scm.set_start_type(_SERVICE_NAMES[component], "demand")
            if result.ok:
                return OperationResult.success(
                    f"{_SERVICE_NAMES[component]} no longer starts automatically.", changed=True
                )
            return OperationResult.failure(
                f"the start type of {_SERVICE_NAMES[component]} could not be changed.",
                detail=result.summary(),
            )

        name = self._task_name(component)
        result = self.tasks.disable(name)
        if result.ok:
            return OperationResult.success(f"the task {name} no longer starts automatically.", changed=True)
        return OperationResult.failure(f"the task {name} could not be disabled.", detail=result.summary())

    def logs(self, component: Component, lines: int = 50) -> OperationResult:
        """Return the tail of the log file of ``component``."""
        log_file = win_paths.log_path()
        if not log_file.exists():
            return OperationResult.success(
                f"{log_file} does not exist yet; the component has not written any log line.",
                data={"output": ""},
            )
        try:
            content = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            return OperationResult.failure(f"{log_file} could not be read.", detail=exc.strerror or str(exc))
        tail = content[-max(1, lines) :]
        return OperationResult.success(
            f"{len(tail)} log lines from {log_file}.", data={"output": "\n".join(tail)}
        )

    # -- installation -----------------------------------------------------

    def install(self, component: Component, *, scope: str = "user") -> OperationResult:
        """Install the component. Idempotent."""
        if scope == "system":
            return self._install_service(component)
        return self._install_task(component)

    def _install_task(self, component: Component) -> OperationResult:
        name = self._task_name(component)
        config = win_paths.config_path()
        try:
            config.parent.mkdir(parents=True, exist_ok=True)
            if not config.exists():
                from dontneed_dns.core.config import config_to_toml, default_config

                config.write_text(config_to_toml(default_config()), encoding="utf-8")
        except OSError as exc:
            return OperationResult.failure(
                f"the configuration {config} could not be written.", detail=exc.strerror or str(exc)
            )

        command = self._command_line(component)
        result = self.tasks.create(name, command, trigger="ONLOGON", run_level="LIMITED")
        if not result.ok:
            return OperationResult.failure(f"the task {name} could not be created.", detail=result.summary())
        return OperationResult.success(f"the task {name} was created.")

    def _install_service(self, component: Component) -> OperationResult:
        if not _is_admin():
            return OperationResult.needs_elevation(
                "Installing a Windows service needs administrator rights.",
                detail="Re-run the command from an elevated terminal.",
            )
        if not self.scm.is_available():
            return OperationResult.not_supported(
                "the Windows service backend needs pywin32, which is not installed.",
                detail="Install pywin32 or use the user scope with a scheduled task.",
            )
        service_name = _SERVICE_NAMES[component]
        if self.scm.exists(service_name):
            return OperationResult.success(f"the service {service_name} already exists.")
        command = self._command_line(component)
        result = self.scm.create(service_name, command, display_name=_DISPLAY_NAMES[component])
        if not result.ok:
            return OperationResult.failure(
                f"the service {service_name} could not be created.", detail=result.summary()
            )
        return OperationResult.success(f"the service {service_name} was created.", changed=True)

    def uninstall(self, component: Component, *, scope: str = "user") -> OperationResult:
        """Remove the component and everything this project owns for it."""
        if scope == "system":
            return self._uninstall_service(component)
        return self._uninstall_task(component)

    def _uninstall_task(self, component: Component) -> OperationResult:
        name = self._task_name(component)
        changed = False
        if self.tasks.exists(name):
            self.tasks.end(name)
            result = self.tasks.delete(name)
            if not result.ok:
                return OperationResult.failure(
                    f"the task {name} could not be removed.", detail=result.summary()
                )
            changed = True
        try:
            self._pid_path(component).unlink()
            changed = True
        except OSError:
            pass
        return OperationResult.success(f"the task {name} was removed.", changed=changed)

    def _uninstall_service(self, component: Component) -> OperationResult:
        if not _is_admin():
            return OperationResult.needs_elevation(
                "Removing a Windows service needs administrator rights.",
                detail="Re-run the command from an elevated terminal.",
            )
        service_name = _SERVICE_NAMES[component]
        if not self.scm.exists(service_name):
            return OperationResult.success(f"the service {service_name} does not exist.")
        self.scm.stop(service_name)
        result = self.scm.delete(service_name)
        if not result.ok:
            return OperationResult.failure(
                f"the service {service_name} could not be removed.", detail=result.summary()
            )
        return OperationResult.success(f"the service {service_name} was removed.", changed=True)

    def install_paths(self, scope: str = "user") -> dict[str, Path]:
        """Return the paths this project writes to."""
        paths = {
            "config": win_paths.config_path(),
            "logs": win_paths.log_dir(),
            "state": win_paths.state_dir(),
            "install": win_paths.install_dir(),
        }
        if scope == "system":
            paths["config"] = win_paths.program_data() / "dontneed-dns" / "config.toml"
            paths["state"] = win_paths.program_data() / "dontneed-dns" / "state"
        return paths
