"""The platform capability matrix.

Every feature of the project is explicitly classified for every platform.
The matrix is data, not documentation prose: the CLI and the TUI render it
and the test-suite asserts that it stays consistent.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "LINUX_MATRIX",
    "WINDOWS_MATRIX",
    "CapabilityEntry",
    "CapabilityLevel",
    "CapabilityMatrix",
    "Feature",
    "matrix_for",
]


class Feature(StrEnum):
    """Every feature that can differ between platforms."""

    DNS_LOCAL_SERVER = "dns_local_server"
    DNS_SPLIT_ROUTING = "dns_split_routing"
    DNS_SYSTEM_FALLBACK = "dns_system_fallback"
    DNS_TCP = "dns_tcp"
    DNS_INTERCEPTION = "dns_interception"
    PROXY_SOCKS5 = "proxy_socks5"
    PROXY_HTTP_CONNECT = "proxy_http_connect"
    PROXY_TLS_FRAGMENTATION = "proxy_tls_fragmentation"
    SERVICE_USER_SCOPE = "service_user_scope"
    SERVICE_SYSTEM_SCOPE = "service_system_scope"
    SERVICE_SCHEDULED_TASK = "service_scheduled_task"
    SERVICE_SCM = "service_scm"
    AUTOSTART = "autostart"
    LOGS_JOURNAL = "logs_journal"
    LOGS_FILE = "logs_file"
    TUI = "tui"
    ELEVATION = "elevation"
    NETWORK_CONFIGURATION = "network_configuration"
    APP_SCOPED_PROXY = "app_scoped_proxy"
    PORTABLE_PACKAGE = "portable_package"
    SELF_CONTAINED_EXECUTABLE = "self_contained_executable"


class CapabilityLevel(StrEnum):
    """How well a feature is implemented on a platform."""

    SUPPORTED = "supported"
    SUPPORTED_WITH_LIMITATIONS = "supported_with_limitations"
    EXPERIMENTAL = "experimental"
    UNSUPPORTED = "unsupported"

    @property
    def label(self) -> str:
        """A human readable label."""
        return {
            CapabilityLevel.SUPPORTED: "Supported",
            CapabilityLevel.SUPPORTED_WITH_LIMITATIONS: "Supported with limitations",
            CapabilityLevel.EXPERIMENTAL: "Experimental",
            CapabilityLevel.UNSUPPORTED: "Unsupported",
        }[self]


@dataclass(frozen=True)
class CapabilityEntry:
    """A single classified feature."""

    feature: Feature
    level: CapabilityLevel
    note: str = ""


@dataclass(frozen=True)
class CapabilityMatrix:
    """All classified features of one platform."""

    platform: str
    entries: tuple[CapabilityEntry, ...]

    def get(self, feature: Feature) -> CapabilityEntry:
        """Return the entry for ``feature``."""
        for entry in self.entries:
            if entry.feature is feature:
                return entry
        raise KeyError(feature)

    def level(self, feature: Feature) -> CapabilityLevel:
        """Return the capability level of ``feature``."""
        return self.get(feature).level

    def is_supported(self, feature: Feature) -> bool:
        """``True`` when the feature works, possibly with limitations."""
        return self.level(feature) in (
            CapabilityLevel.SUPPORTED,
            CapabilityLevel.SUPPORTED_WITH_LIMITATIONS,
        )

    def as_rows(self) -> list[tuple[str, str, str]]:
        """Return ``(feature, level, note)`` rows for rendering."""
        return [(e.feature.value, e.level.label, e.note) for e in self.entries]


LINUX_MATRIX = CapabilityMatrix(
    platform="linux",
    entries=(
        CapabilityEntry(Feature.DNS_LOCAL_SERVER, CapabilityLevel.SUPPORTED),
        CapabilityEntry(Feature.DNS_SPLIT_ROUTING, CapabilityLevel.SUPPORTED),
        CapabilityEntry(
            Feature.DNS_SYSTEM_FALLBACK,
            CapabilityLevel.SUPPORTED,
            "Forwarded to the nameservers in /etc/resolv.conf.",
        ),
        CapabilityEntry(Feature.DNS_TCP, CapabilityLevel.SUPPORTED),
        CapabilityEntry(
            Feature.DNS_INTERCEPTION,
            CapabilityLevel.SUPPORTED_WITH_LIMITATIONS,
            "Requires an explicit opt-in; scoped to a single interface or user.",
        ),
        CapabilityEntry(Feature.PROXY_SOCKS5, CapabilityLevel.SUPPORTED),
        CapabilityEntry(Feature.PROXY_HTTP_CONNECT, CapabilityLevel.SUPPORTED),
        CapabilityEntry(
            Feature.PROXY_TLS_FRAGMENTATION,
            CapabilityLevel.EXPERIMENTAL,
            "Opt-in; no guarantee that a specific DPI is bypassed.",
        ),
        CapabilityEntry(Feature.SERVICE_USER_SCOPE, CapabilityLevel.SUPPORTED, "systemd --user units."),
        CapabilityEntry(
            Feature.SERVICE_SYSTEM_SCOPE,
            CapabilityLevel.SUPPORTED,
            "systemd system units; requires administrator privileges.",
        ),
        CapabilityEntry(Feature.SERVICE_SCHEDULED_TASK, CapabilityLevel.UNSUPPORTED),
        CapabilityEntry(Feature.SERVICE_SCM, CapabilityLevel.UNSUPPORTED),
        CapabilityEntry(Feature.AUTOSTART, CapabilityLevel.SUPPORTED, "systemd enable/disable."),
        CapabilityEntry(Feature.LOGS_JOURNAL, CapabilityLevel.SUPPORTED),
        CapabilityEntry(Feature.LOGS_FILE, CapabilityLevel.SUPPORTED),
        CapabilityEntry(Feature.TUI, CapabilityLevel.SUPPORTED),
        CapabilityEntry(
            Feature.ELEVATION, CapabilityLevel.SUPPORTED, "Only for the operations that need it."
        ),
        CapabilityEntry(
            Feature.NETWORK_CONFIGURATION,
            CapabilityLevel.SUPPORTED_WITH_LIMITATIONS,
            "systemd-resolved split DNS or an owner matched firewall rule; always reversible.",
        ),
        CapabilityEntry(Feature.APP_SCOPED_PROXY, CapabilityLevel.SUPPORTED, "dontneed-dns launch"),
        CapabilityEntry(
            Feature.PORTABLE_PACKAGE, CapabilityLevel.SUPPORTED, "Source archive plus a wheelhouse."
        ),
        CapabilityEntry(
            Feature.SELF_CONTAINED_EXECUTABLE,
            CapabilityLevel.UNSUPPORTED,
            "A self contained Linux binary is not shipped; the archive installs into a virtual environment.",
        ),
    ),
)

WINDOWS_MATRIX = CapabilityMatrix(
    platform="windows",
    entries=(
        CapabilityEntry(Feature.DNS_LOCAL_SERVER, CapabilityLevel.SUPPORTED),
        CapabilityEntry(Feature.DNS_SPLIT_ROUTING, CapabilityLevel.SUPPORTED),
        CapabilityEntry(
            Feature.DNS_SYSTEM_FALLBACK,
            CapabilityLevel.SUPPORTED_WITH_LIMITATIONS,
            "Uses the Windows resolver; only A and AAAA records are synthesised.",
        ),
        CapabilityEntry(Feature.DNS_TCP, CapabilityLevel.SUPPORTED),
        CapabilityEntry(
            Feature.DNS_INTERCEPTION,
            CapabilityLevel.UNSUPPORTED,
            "Requires a kernel driver; instead point an application at the local proxy.",
        ),
        CapabilityEntry(Feature.PROXY_SOCKS5, CapabilityLevel.SUPPORTED),
        CapabilityEntry(Feature.PROXY_HTTP_CONNECT, CapabilityLevel.SUPPORTED),
        CapabilityEntry(
            Feature.PROXY_TLS_FRAGMENTATION,
            CapabilityLevel.EXPERIMENTAL,
            "Opt-in; no guarantee that a specific DPI is bypassed.",
        ),
        CapabilityEntry(
            Feature.SERVICE_USER_SCOPE,
            CapabilityLevel.SUPPORTED,
            "A scheduled task runs the component in the background.",
        ),
        CapabilityEntry(
            Feature.SERVICE_SYSTEM_SCOPE,
            CapabilityLevel.SUPPORTED_WITH_LIMITATIONS,
            "A Windows service; requires administrator privileges and pywin32.",
        ),
        CapabilityEntry(Feature.SERVICE_SCHEDULED_TASK, CapabilityLevel.SUPPORTED),
        CapabilityEntry(Feature.SERVICE_SCM, CapabilityLevel.SUPPORTED, "Optional; needs pywin32."),
        CapabilityEntry(Feature.AUTOSTART, CapabilityLevel.SUPPORTED, "Scheduled task enable/disable."),
        CapabilityEntry(Feature.LOGS_JOURNAL, CapabilityLevel.UNSUPPORTED),
        CapabilityEntry(Feature.LOGS_FILE, CapabilityLevel.SUPPORTED),
        CapabilityEntry(Feature.TUI, CapabilityLevel.SUPPORTED),
        CapabilityEntry(
            Feature.ELEVATION,
            CapabilityLevel.SUPPORTED,
            "Only the installation of a system service asks for elevation.",
        ),
        CapabilityEntry(
            Feature.NETWORK_CONFIGURATION,
            CapabilityLevel.UNSUPPORTED,
            "System wide DNS, firewall and routing tables are never modified.",
        ),
        CapabilityEntry(
            Feature.APP_SCOPED_PROXY,
            CapabilityLevel.SUPPORTED,
            "dontneed-dns launch sets the proxy for the launched process only.",
        ),
        CapabilityEntry(Feature.PORTABLE_PACKAGE, CapabilityLevel.SUPPORTED),
        CapabilityEntry(
            Feature.SELF_CONTAINED_EXECUTABLE,
            CapabilityLevel.SUPPORTED,
            "A PyInstaller executable ships with the Windows archive.",
        ),
    ),
)

_MATRICES = {"linux": LINUX_MATRIX, "windows": WINDOWS_MATRIX}


def matrix_for(platform: str) -> CapabilityMatrix:
    """Return the capability matrix of ``platform``."""
    try:
        return _MATRICES[platform.lower()]
    except KeyError as exc:
        raise KeyError(f"no capability matrix for platform {platform!r}") from exc
