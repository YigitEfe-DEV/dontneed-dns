#!/usr/bin/env python3
"""Verify the release archives before they are published.

The script checks every archive of a directory: the contents have to be
complete, the files of the other platform must not be present and the
checksums in ``SHA256SUMS`` have to match.

Usage::

    python scripts/verify_release.py dist
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_release import FORBIDDEN_PATTERNS

REQUIRED_ENTRIES = ("README.md", "LICENSE", "VERSION")
SOURCE_ENTRY = "src/dontneed_dns/__init__.py"


def sha256_of(path: Path) -> str:
    """Return the SHA-256 checksum of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def platform_of(name: str) -> str | None:
    """Return the platform an archive belongs to."""
    if "linux" in name:
        return "linux"
    if "windows" in name:
        return "windows"
    return None


def verify_archive(archive: Path) -> list[str]:
    """Return the problems of one archive."""
    problems: list[str] = []
    platform = platform_of(archive.name)
    if platform is None:
        problems.append(f"{archive.name}: the platform cannot be recognised")
        return problems

    with zipfile.ZipFile(archive) as handle:
        names = handle.namelist()
        if not names:
            problems.append(f"{archive.name}: the archive is empty")
            return problems

        for name in names:
            for pattern in FORBIDDEN_PATTERNS:
                if (pattern.endswith("/") and (name.startswith(pattern) or f"/{pattern}" in f"/{name}")) or (
                    not pattern.endswith("/") and Path(name).match(pattern)
                ):
                    problems.append(f"{archive.name}: the forbidden entry {name}")

        foreign = (".ps1", ".exe") if platform == "linux" else (".sh",)
        for name in names:
            if name.endswith(foreign):
                problems.append(f"{archive.name}: the file of the other platform {name}")

        for required in (*REQUIRED_ENTRIES, SOURCE_ENTRY):
            if not any(name.endswith(required) for name in names):
                problems.append(f"{archive.name}: {required} is missing")

        broken = handle.testzip()
        if broken is not None:
            problems.append(f"{archive.name}: the entry {broken} is corrupt")

    return problems


def verify_checksums(directory: Path) -> list[str]:
    """Compare the archives with the ``SHA256SUMS`` file."""
    problems: list[str] = []
    checksum_file = directory / "SHA256SUMS"
    if not checksum_file.exists():
        return [f"{checksum_file.name} is missing"]

    expected: dict[str, str] = {}
    for line in checksum_file.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2:
            expected[parts[1].lstrip("*")] = parts[0]

    for name, checksum in expected.items():
        path = directory / name
        if not path.exists():
            problems.append(f"{name} is listed in {checksum_file.name} but does not exist")
            continue
        actual = sha256_of(path)
        if actual != checksum:
            problems.append(f"{name}: the checksum does not match ({actual} != {checksum})")

    return problems


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="The directory with the archives.")
    args = parser.parse_args()

    if not args.directory.is_dir():
        print(f"error: {args.directory} is not a directory", file=sys.stderr)
        return 1

    archives = sorted(args.directory.glob("*.zip"))
    if not archives:
        print(f"error: no archive was found in {args.directory}", file=sys.stderr)
        return 1

    problems: list[str] = []
    for archive in archives:
        found = verify_archive(archive)
        problems.extend(found)
        state = "ok" if not found else f"{len(found)} problem(s)"
        print(f"{archive.name}: {state}")

    problems.extend(verify_checksums(args.directory))

    if problems:
        print("\nthe release is not ready:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    print("\nevery archive is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
