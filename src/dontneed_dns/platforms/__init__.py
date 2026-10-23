"""Platform detection and adapter construction."""

from __future__ import annotations

import sys

from dontneed_dns.core.errors import UnsupportedPlatformError
from dontneed_dns.platforms.base import PlatformAdapter

__all__ = ["create_adapter", "detect_platform", "supported_platforms"]


def detect_platform() -> str:
    """Return the identifier of the running platform.

    Only the two platforms this project supports are recognised; anything
    else raises :class:`UnsupportedPlatformError` so that shared code never
    silently runs platform specific commands.
    """
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform.startswith("win") or sys.platform.startswith("cygwin"):
        return "windows"
    raise UnsupportedPlatformError(
        f"dontneed-dns supports Linux and Windows; {sys.platform} is not supported"
    )


def supported_platforms() -> tuple[str, ...]:
    """The identifiers of the supported platforms."""
    return ("linux", "windows")


def create_adapter() -> PlatformAdapter:
    """Create the adapter matching the running operating system."""
    platform = detect_platform()
    if platform == "linux":
        from dontneed_dns.platforms.linux.adapter import LinuxPlatformAdapter

        return LinuxPlatformAdapter()
    if platform == "windows":
        from dontneed_dns.platforms.windows.adapter import WindowsPlatformAdapter

        return WindowsPlatformAdapter()
    raise UnsupportedPlatformError(f"no adapter for platform {platform!r}")
