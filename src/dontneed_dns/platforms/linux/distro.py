"""Distribution detection for the Linux edition."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = ["DistroInfo", "detect_distro", "is_known_distro"]

OS_RELEASE = "/etc/os-release"

#: Distributions the project was tested on or is known to work on.
KNOWN_DISTROS = {
    "cachyos",
    "arch",
    "archarm",
    "manjaro",
    "endeavouros",
    "ubuntu",
    "debian",
    "fedora",
    "rhel",
    "centos",
    "rocky",
    "alma",
    "opensuse",
    "opensuse-tumbleweed",
    "suse",
}


@dataclass(frozen=True)
class DistroInfo:
    """What ``/etc/os-release`` reports."""

    identifier: str = "unknown"
    name: str = "unknown"
    version: str = ""
    id_like: tuple[str, ...] = ()
    path: str = OS_RELEASE

    @property
    def family(self) -> str:
        """The distribution family (``arch``, ``debian``, ``fedora`` ...)."""
        if self.identifier in ("cachyos", "arch", "manjaro", "endeavouros"):
            return "arch"
        if self.identifier in ("ubuntu", "debian"):
            return "debian"
        if self.identifier in ("fedora", "rhel", "centos", "rocky", "alma"):
            return "fedora"
        if self.identifier.startswith("opensuse") or self.identifier == "suse":
            return "suse"
        return self.identifier

    @property
    def is_known(self) -> bool:
        """``True`` when the distribution is on the known list."""
        candidates = {self.identifier, *self.id_like}
        return bool(candidates & KNOWN_DISTROS)


def detect_distro(path: str = OS_RELEASE) -> DistroInfo:
    """Read the distribution information from ``/etc/os-release``."""
    try:
        content = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return DistroInfo(path=path)

    values: dict[str, str] = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")

    identifier = values.get("ID", "unknown").lower()
    id_like_raw = values.get("ID_LIKE", "")
    id_like = tuple(part.strip().lower() for part in id_like_raw.split() if part.strip())

    return DistroInfo(
        identifier=identifier,
        name=values.get("PRETTY_NAME", identifier),
        version=values.get("VERSION_ID", ""),
        id_like=id_like,
        path=path,
    )


def is_known_distro(info: DistroInfo) -> bool:
    """``True`` when the distribution is supported."""
    return info.is_known
