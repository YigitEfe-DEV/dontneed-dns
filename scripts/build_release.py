#!/usr/bin/env python3
"""Build the release archives of dontneed-dns.

The Linux and the Windows archives are built separately and never contain
resources of the other platform.  Every archive is verified before it is
written: development files, caches, virtual environments and personal data
have to be absent.

Usage::

    python scripts/build_release.py --platform linux --output dist
    python scripts/build_release.py --platform windows --output dist
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PACKAGING_DIR = PROJECT_ROOT / "packaging"

ARCHIVE_ROOT_NAME = "dontneed-dns-{platform}-x86_64"

#: Patterns that must never appear inside a release archive.
FORBIDDEN_PATTERNS = (
    ".git/",
    ".venv/",
    "venv/",
    "__pycache__/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".mypy_cache/",
    ".tox/",
    "node_modules/",
    "*.pyc",
    "*.pyo",
    "*.egg-info/",
    ".env",
    "*.log",
    "*.pid",
    ".DS_Store",
    "Thumbs.db",
)

#: Files that are copied into every archive.
COMMON_FILES = (
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
    "SECURITY.md",
)

#: Documentation that is shipped with each platform.
PLATFORM_DOCS = {
    "linux": ("docs/linux.md", "docs/architecture.md", "docs/security.md", "docs/troubleshooting.md"),
    "windows": ("docs/windows.md", "docs/architecture.md", "docs/security.md", "docs/troubleshooting.md"),
}

WHEELHOUSE_REQUIREMENTS = ("typer", "rich", "textual")


def run(argv: list[str], cwd: Path | None = None) -> None:
    """Run a command, raising on a failure."""
    print(f"  $ {' '.join(argv)}")
    completed = subprocess.run(argv, cwd=str(cwd) if cwd else None, check=False)
    if completed.returncode != 0:
        raise SystemExit(f"command failed with status {completed.returncode}: {' '.join(argv)}")


def pip_command() -> list[str]:
    """Return the command that runs pip.

    The interpreter of the current virtual environment does not always have
    pip installed (``uv venv`` does not add it), so the pip of the system is
    used as a fallback.
    """
    probe = subprocess.run(
        [sys.executable, "-m", "pip", "--version"], capture_output=True, text=True, check=False
    )
    if probe.returncode == 0:
        return [sys.executable, "-m", "pip"]
    executable = shutil.which("pip3") or shutil.which("pip")
    if executable:
        return [executable]
    raise SystemExit("pip was not found; install it or use a virtual environment that has it")


def sha256_of(path: Path) -> str:
    """Return the SHA-256 checksum of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_wheelhouse(target: Path, *, platform: str) -> None:
    """Download the runtime dependencies as wheels."""
    target.mkdir(parents=True, exist_ok=True)
    command = [
        *pip_command(),
        "download",
        "--only-binary=:all:",
        "--dest",
        str(target),
        *WHEELHOUSE_REQUIREMENTS,
    ]
    if platform == "windows":
        command.extend(["--platform", "win_amd64", "--python-version", "3.11", "--implementation", "cp"])
    run(command)


def build_project_wheel(target: Path) -> None:
    """Build the wheel of the project itself."""
    target.mkdir(parents=True, exist_ok=True)
    run(
        [
            *pip_command(),
            "wheel",
            "--no-deps",
            "--wheel-dir",
            str(target),
            str(PROJECT_ROOT),
        ]
    )


def copy_source(target: Path) -> None:
    """Copy the source of the project into the archive."""
    source = target / "src"
    shutil.copytree(
        PROJECT_ROOT / "src" / "dontneed_dns",
        source / "dontneed_dns",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )


def assemble_tree(root: Path, platform: str) -> None:
    """Fill the archive directory with the platform specific resources."""
    root.mkdir(parents=True, exist_ok=True)

    for name in COMMON_FILES:
        source = PROJECT_ROOT / name
        if source.exists():
            shutil.copy2(source, root / name)

    version = read_version()
    (root / "VERSION").write_text(version + "\n", encoding="utf-8")

    docs_target = root / "docs"
    docs_target.mkdir(exist_ok=True)
    for relative in PLATFORM_DOCS[platform]:
        source = PROJECT_ROOT / relative
        if source.exists():
            shutil.copy2(source, docs_target / Path(relative).name)

    copy_source(root)

    packaging = PACKAGING_DIR / platform
    for name in ("install.sh", "uninstall.sh", "install.ps1", "uninstall.ps1"):
        source = packaging / name
        if source.exists():
            shutil.copy2(source, root / name)
            if name.endswith(".sh"):
                (root / name).chmod(0o755)


def read_version() -> str:
    """Read the version from ``pyproject.toml``."""
    for line in (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("version"):
            return stripped.split("=", 1)[1].strip().strip('"')
    raise SystemExit("the version could not be found in pyproject.toml")


def verify_tree(root: Path) -> list[str]:
    """Return the list of forbidden files that are present in the tree."""
    problems: list[str] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        for pattern in FORBIDDEN_PATTERNS:
            if pattern.endswith("/"):
                if relative.startswith(pattern) or f"/{pattern}" in f"/{relative}":
                    problems.append(relative)
                    break
            elif path.match(pattern):
                problems.append(relative)
                break
    return problems


def build_archive(
    platform: str,
    output: Path,
    *,
    with_wheelhouse: bool = True,
    executable: Path | None = None,
) -> Path:
    """Build the release archive of one platform."""
    output.mkdir(parents=True, exist_ok=True)
    archive_name = ARCHIVE_ROOT_NAME.format(platform=platform)
    archive_path = output / f"{archive_name}.zip"

    with tempfile.TemporaryDirectory(prefix="dontneed-dns-build-") as temporary:
        staging = Path(temporary) / archive_name
        print(f"building {archive_name}")
        assemble_tree(staging, platform)

        if with_wheelhouse:
            print("  collecting the wheelhouse")
            wheels = staging / "wheels"
            build_wheelhouse(wheels, platform=platform)
            build_project_wheel(wheels)

        if executable is not None:
            print(f"  adding the executable {executable.name}")
            shutil.copy2(executable, staging / executable.name)

        problems = verify_tree(staging)
        if problems:
            raise SystemExit(
                "the archive would contain forbidden files:\n  " + "\n  ".join(sorted(set(problems)))
            )

        if archive_path.exists():
            archive_path.unlink()
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(staging.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(staging.parent))

    verify_archive(archive_path, platform)
    print(f"  {archive_path} ({archive_path.stat().st_size // 1024} KiB)")
    return archive_path


def verify_archive(archive: Path, platform: str) -> None:
    """Check the contents of a finished archive."""
    with zipfile.ZipFile(archive) as handle:
        names = handle.namelist()
        if not names:
            raise SystemExit(f"{archive} is empty")

        for name in names:
            for pattern in FORBIDDEN_PATTERNS:
                if pattern.endswith("/") and (name.startswith(pattern) or f"/{pattern}" in f"/{name}"):
                    raise SystemExit(f"{archive} contains the forbidden entry {name}")
                if not pattern.endswith("/") and Path(name).match(pattern):
                    raise SystemExit(f"{archive} contains the forbidden entry {name}")

        foreign = {
            "linux": (".ps1", ".exe"),
            "windows": (".sh",),
        }[platform]
        for name in names:
            if name.endswith(foreign):
                raise SystemExit(f"{archive} contains a file of the other platform: {name}")

        expected = {"README.md", "LICENSE", "VERSION", "src/dontneed_dns/__init__.py"}
        missing = {entry for entry in expected if not any(name.endswith(entry) for name in names)}
        if missing:
            raise SystemExit(f"{archive} is missing: {', '.join(sorted(missing))}")


def write_checksums(paths: list[Path], output: Path) -> Path:
    """Write a ``SHA256SUMS`` file next to the archives."""
    checksum_file = output / "SHA256SUMS"
    lines = [f"{sha256_of(path)}  {path.name}" for path in sorted(paths)]
    checksum_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return checksum_file


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--platform",
        choices=("linux", "windows", "all"),
        default="all",
        help="The platform to build.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "dist",
        help="The directory the archives are written to.",
    )
    parser.add_argument(
        "--no-wheelhouse",
        action="store_true",
        help="Skip the wheelhouse (used for a quick local build).",
    )
    parser.add_argument(
        "--executable",
        type=Path,
        default=None,
        help="A self contained executable to add to the Windows archive.",
    )
    args = parser.parse_args()

    platforms = ("linux", "windows") if args.platform == "all" else (args.platform,)
    archives = [
        build_archive(
            platform,
            args.output,
            with_wheelhouse=not args.no_wheelhouse,
            executable=args.executable if platform == "windows" else None,
        )
        for platform in platforms
    ]

    checksum_file = write_checksums(archives, args.output)
    print(f"\nchecksums written to {checksum_file}")
    for path in archives:
        print(f"  {sha256_of(path)}  {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
