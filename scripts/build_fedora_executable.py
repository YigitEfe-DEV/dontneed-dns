#!/usr/bin/env python3
"""Build the self contained Fedora edition executable.

The result is a single file that carries the application, the interpreter
and every dependency.  It has to be built on Fedora, because a binary that
is linked against another distribution's C library does not run everywhere.

Usage::

    python scripts/build_fedora_executable.py --output dist

The script refuses to build on a host that is not Fedora unless
``--allow-non-fedora`` is given, so that an incompatible binary cannot be
produced by accident.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENTRY_POINT = ROOT / "packaging" / "linux" / "fedora_entry.py"
EXECUTABLE_NAME = "dontneed-dns-fedora-x86_64"
SUPPORTED_ARCHITECTURES = ("x86_64", "amd64")


def read_version() -> str:
    """Read the version from ``pyproject.toml``."""
    for line in (ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("version"):
            return stripped.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("the version could not be read from pyproject.toml")


def sha256_of(path: Path) -> str:
    """Return the SHA-256 checksum of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def check_host(allow_non_fedora: bool) -> str:
    """Verify that the build host can produce a compatible binary."""
    machine = platform.machine().lower()
    if machine not in SUPPORTED_ARCHITECTURES:
        raise SystemExit(f"this build script only produces x86_64 binaries, this host is {machine}")
    os_release = Path("/etc/os-release")
    identifier = ""
    if os_release.exists():
        for line in os_release.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("ID="):
                identifier = line.split("=", 1)[1].strip().strip('"')
                break
    if identifier != "fedora" and not allow_non_fedora:
        raise SystemExit(
            f"the build host is {identifier or 'unknown'}, not Fedora; "
            "build the executable on Fedora or pass --allow-non-fedora"
        )
    return identifier or "unknown"


def build(output: Path, work_path: Path, *, allow_non_fedora: bool) -> Path:
    """Run PyInstaller and return the path of the produced executable."""
    identifier = check_host(allow_non_fedora)
    print(f"building the Fedora edition on {identifier}")

    if shutil.which("pyinstaller") is None:
        raise SystemExit("pyinstaller is not installed (pip install pyinstaller)")

    output.mkdir(parents=True, exist_ok=True)
    work_path.mkdir(parents=True, exist_ok=True)

    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT / "src")

    command = [
        "pyinstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--console",
        "--name",
        EXECUTABLE_NAME,
        "--distpath",
        str(output),
        "--workpath",
        str(work_path / "build"),
        "--specpath",
        str(work_path),
        "--collect-all",
        "dontneed_dns",
        "--collect-all",
        "textual",
        "--collect-all",
        "typer",
        "--collect-all",
        "rich",
        "--paths",
        str(ROOT / "src"),
        str(ENTRY_POINT),
    ]
    print(" ".join(command))
    subprocess.run(command, check=True, env=environment, cwd=str(ROOT))

    executable = output / EXECUTABLE_NAME
    if not executable.exists():
        raise SystemExit(f"the build did not produce {executable}")
    executable.chmod(0o755)
    return executable


def verify(executable: Path) -> None:
    """Run the produced executable and check that it answers."""
    for arguments, expected in ((["--version"], "dontneed-dns"), (["--help"], "Usage:")):
        result = subprocess.run(
            [str(executable), *arguments],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0 or expected not in result.stdout:
            raise SystemExit(
                f"the executable did not answer to {arguments}: "
                f"exit {result.returncode}\n{result.stdout}\n{result.stderr}"
            )
        print(f"verified: {executable.name} {arguments[0]}")


def write_checksum(executable: Path, output: Path) -> Path:
    """Write the checksum of the executable into ``SHA256SUMS``."""
    sums = output / "SHA256SUMS"
    lines = []
    if sums.exists():
        for line in sums.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.endswith(f"  {executable.name}"):
                lines.append(line)
    lines.append(f"{sha256_of(executable)}  {executable.name}")
    sums.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sums


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="dist", help="the directory the executable is written to")
    parser.add_argument(
        "--work-path",
        default="build/fedora",
        help="the directory PyInstaller uses for its intermediate files",
    )
    parser.add_argument(
        "--allow-non-fedora",
        action="store_true",
        help="build even when the host is not Fedora (the result may not run everywhere)",
    )
    parser.add_argument("--skip-verify", action="store_true", help="do not run the produced executable")
    arguments = parser.parse_args(argv)

    output = (
        (ROOT / arguments.output).resolve()
        if not Path(arguments.output).is_absolute()
        else Path(arguments.output)
    )
    work_path = (
        (ROOT / arguments.work_path).resolve()
        if not Path(arguments.work_path).is_absolute()
        else Path(arguments.work_path)
    )

    executable = build(output, work_path, allow_non_fedora=arguments.allow_non_fedora)
    if not arguments.skip_verify:
        verify(executable)
    sums = write_checksum(executable, output)
    print(f"executable: {executable}")
    print(f"size: {executable.stat().st_size // (1024 * 1024)} MiB")
    print(f"checksums: {sums}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
