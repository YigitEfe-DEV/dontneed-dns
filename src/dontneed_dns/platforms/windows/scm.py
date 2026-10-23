"""Windows Service Control Manager backend.

This backend installs the components as real Windows services.  It is
optional because it needs two things the portable build does not ship:

* administrator rights to create a service, and
* ``pywin32`` so that the Python process can answer the SCM control
  requests.

When ``pywin32`` is missing the backend reports that it is unavailable
instead of failing with an opaque error.
"""

from __future__ import annotations

from dataclasses import dataclass

from dontneed_dns.core.system import CommandResult, run_command, which

__all__ = ["ServiceControlManagerBackend", "ServiceInfo", "pywin32_available"]


def pywin32_available() -> bool:
    """``True`` when the ``pywin32`` package can be imported."""
    try:
        import win32serviceutil  # noqa: F401
    except Exception:  # noqa: BLE001 - depends on the host
        return False
    return True


@dataclass(frozen=True)
class ServiceInfo:
    """What is known about a Windows service."""

    exists: bool = False
    running: bool = False
    start_type: str = "unknown"


class ServiceControlManagerBackend:
    """Create and control Windows services through ``sc.exe``."""

    def __init__(self) -> None:
        self._service_names: dict[str, str] = {}

    def register(self, component: str, service_name: str) -> None:
        """Remember the service name of ``component``."""
        self._service_names[component] = service_name

    def service_name(self, component: str) -> str:
        """The service name of ``component``."""
        return self._service_names.get(component, f"dontneed-dns-{component}")

    def is_available(self) -> bool:
        """``True`` when the backend can be used on this machine."""
        return which("sc") is not None and pywin32_available()

    # -- state ------------------------------------------------------------

    def query(self, service_name: str) -> CommandResult:
        """Query the service."""
        return run_command(["sc", "query", service_name], timeout=30)

    def exists(self, service_name: str) -> bool:
        """``True`` when the service is registered."""
        result = self.query(service_name)
        return result.ok and "1060" not in result.stderr

    def info(self, service_name: str) -> ServiceInfo:
        """Collect the state of the service."""
        if not self.exists(service_name):
            return ServiceInfo(exists=False)
        result = self.query(service_name)
        running = "RUNNING" in result.stdout.upper()
        start_type = "unknown"
        config = run_command(["sc", "qc", service_name], timeout=30)
        if config.ok:
            for line in config.stdout.splitlines():
                if "START_TYPE" in line.upper():
                    start_type = line.split(":", 1)[-1].strip()
                    break
        return ServiceInfo(exists=True, running=running, start_type=start_type)

    # -- actions ----------------------------------------------------------

    def create(self, service_name: str, binary_path: str, *, display_name: str) -> CommandResult:
        """Create the service."""
        return run_command(
            [
                "sc",
                "create",
                service_name,
                f"binPath= {binary_path}",
                "start=",
                "auto",
                f"DisplayName= {display_name}",
            ],
            timeout=60,
        )

    def start(self, service_name: str) -> CommandResult:
        """Start the service."""
        return run_command(["sc", "start", service_name], timeout=60)

    def stop(self, service_name: str) -> CommandResult:
        """Stop the service."""
        return run_command(["sc", "stop", service_name], timeout=60)

    def delete(self, service_name: str) -> CommandResult:
        """Remove the service."""
        return run_command(["sc", "delete", service_name], timeout=60)

    def set_start_type(self, service_name: str, start_type: str) -> CommandResult:
        """Change the start type (``auto`` or ``demand``)."""
        return run_command(["sc", "config", service_name, "start=", start_type], timeout=60)
