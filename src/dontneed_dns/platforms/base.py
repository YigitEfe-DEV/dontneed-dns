"""The platform abstraction layer.

Shared code never calls ``systemctl``, ``schtasks`` or ``sc.exe`` directly.
It asks a :class:`PlatformAdapter` for what it needs, and every adapter
returns a structured :class:`OperationResult` instead of raising when an
operation is not possible on the current platform.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from dontneed_dns.core.capability import CapabilityMatrix

__all__ = [
    "Component",
    "OperationResult",
    "PlatformAdapter",
    "ServiceState",
]


class Component(StrEnum):
    """The two managed components."""

    DNS = "dns"
    PROXY = "proxy"

    @property
    def label(self) -> str:
        """A human readable label."""
        return "DNS resolver" if self is Component.DNS else "Proxy"


class ServiceState(StrEnum):
    """The lifecycle state of a managed component."""

    NOT_INSTALLED = "not_installed"
    INSTALLED = "installed"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        """A human readable label."""
        return {
            ServiceState.NOT_INSTALLED: "Not installed",
            ServiceState.INSTALLED: "Installed",
            ServiceState.RUNNING: "Running",
            ServiceState.STOPPED: "Stopped",
            ServiceState.FAILED: "Failed",
            ServiceState.UNKNOWN: "Unknown",
        }[self]


@dataclass
class OperationResult:
    """The outcome of a platform operation."""

    ok: bool
    message: str
    detail: str = ""
    changed: bool = False
    requires_elevation: bool = False
    unsupported: bool = False
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(cls, message: str, **kwargs: Any) -> OperationResult:
        """Build a successful result."""
        return cls(ok=True, message=message, **kwargs)

    @classmethod
    def failure(cls, message: str, **kwargs: Any) -> OperationResult:
        """Build a failed result."""
        return cls(ok=False, message=message, **kwargs)

    @classmethod
    def needs_elevation(cls, message: str, **kwargs: Any) -> OperationResult:
        """Build a result stating that administrator rights are required."""
        kwargs.setdefault("requires_elevation", True)
        return cls(ok=False, message=message, **kwargs)

    @classmethod
    def not_supported(cls, message: str, **kwargs: Any) -> OperationResult:
        """Build a result stating that the feature does not exist here."""
        kwargs.setdefault("unsupported", True)
        return cls(ok=False, message=message, **kwargs)


class PlatformAdapter(ABC):
    """The contract every platform edition implements."""

    #: Short platform identifier (``linux`` / ``windows``).
    name: str = "unknown"

    # -- introspection ----------------------------------------------------

    @abstractmethod
    def is_available(self) -> bool:
        """``True`` when this adapter can manage components on this machine."""

    @abstractmethod
    def describe(self) -> str:
        """A one line description of the host (distribution, Windows version)."""

    @abstractmethod
    def capabilities(self) -> CapabilityMatrix:
        """The capability matrix of this platform."""

    @abstractmethod
    def diagnostics(self) -> dict[str, Any]:
        """A redacted dictionary describing the current installation."""

    # -- lifecycle --------------------------------------------------------

    @abstractmethod
    def status(self, component: Component) -> ServiceState:
        """Return the current state of ``component``."""

    @abstractmethod
    def is_enabled(self, component: Component) -> bool | None:
        """Return whether ``component`` starts automatically, or ``None``."""

    @abstractmethod
    def start(self, component: Component) -> OperationResult:
        """Start ``component``."""

    @abstractmethod
    def stop(self, component: Component) -> OperationResult:
        """Stop ``component``."""

    @abstractmethod
    def restart(self, component: Component) -> OperationResult:
        """Restart ``component``."""

    @abstractmethod
    def enable(self, component: Component) -> OperationResult:
        """Make ``component`` start automatically."""

    @abstractmethod
    def disable(self, component: Component) -> OperationResult:
        """Stop ``component`` from starting automatically."""

    @abstractmethod
    def logs(self, component: Component, lines: int = 50) -> OperationResult:
        """Return the most recent log output of ``component``."""

    # -- installation -----------------------------------------------------

    @abstractmethod
    def install(self, component: Component, *, scope: str = "user") -> OperationResult:
        """Install ``component``. The operation must be idempotent."""

    @abstractmethod
    def uninstall(self, component: Component, *, scope: str = "user") -> OperationResult:
        """Remove ``component`` and everything this project owns for it."""

    @abstractmethod
    def install_paths(self, scope: str = "user") -> dict[str, Path]:
        """Return the paths this project writes to for ``scope``."""
