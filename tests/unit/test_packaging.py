"""Tests of the release packaging."""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

import build_release


@pytest.fixture
def staged(tmp_path: Path):
    """A staged Linux archive tree."""
    root = tmp_path / "dontneed-dns-linux-x86_64"
    build_release.assemble_tree(root, "linux")
    return root


def test_the_staged_tree_holds_the_expected_files(staged: Path) -> None:
    names = {path.relative_to(staged).as_posix() for path in staged.rglob("*") if path.is_file()}
    assert "README.md" in names
    assert "LICENSE" in names
    assert "VERSION" in names
    assert "src/dontneed_dns/__init__.py" in names
    assert "install.sh" in names
    assert "uninstall.sh" in names
    assert "docs/linux.md" in names or "docs/architecture.md" in names


def test_the_staged_tree_holds_no_caches(staged: Path) -> None:
    assert build_release.verify_tree(staged) == []


def test_the_staged_tree_holds_no_windows_files(staged: Path) -> None:
    names = {path.name for path in staged.rglob("*")}
    assert "install.ps1" not in names
    assert not any(name.endswith(".exe") for name in names)


def test_the_windows_tree_holds_no_linux_files(tmp_path: Path) -> None:
    root = tmp_path / "dontneed-dns-windows-x86_64"
    build_release.assemble_tree(root, "windows")
    names = {path.name for path in root.rglob("*")}
    assert "install.sh" not in names


def test_the_forbidden_files_are_detected(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "config").write_text("x", encoding="utf-8")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "a.pyc").write_bytes(b"x")
    (root / "secret.log").write_text("x", encoding="utf-8")
    problems = build_release.verify_tree(root)
    assert any(".git" in problem for problem in problems)
    assert any("__pycache__" in problem for problem in problems)
    assert any("secret.log" in problem for problem in problems)


def test_an_archive_with_a_forbidden_file_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "dontneed-dns-linux-x86_64.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("dontneed-dns-linux-x86_64/__pycache__/a.pyc", "x")
    with pytest.raises(SystemExit, match="forbidden entry"):
        build_release.verify_archive(archive, "linux")


def test_an_archive_with_a_foreign_file_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "dontneed-dns-linux-x86_64.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("dontneed-dns-linux-x86_64/install.ps1", "x")
    with pytest.raises(SystemExit, match="other platform"):
        build_release.verify_archive(archive, "linux")


def test_an_archive_without_the_essentials_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "dontneed-dns-linux-x86_64.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("dontneed-dns-linux-x86_64/other.txt", "x")
    with pytest.raises(SystemExit, match="missing"):
        build_release.verify_archive(archive, "linux")


def test_a_complete_archive_is_accepted(tmp_path: Path) -> None:
    archive = tmp_path / "dontneed-dns-linux-x86_64.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("dontneed-dns-linux-x86_64/README.md", "x")
        handle.writestr("dontneed-dns-linux-x86_64/LICENSE", "x")
        handle.writestr("dontneed-dns-linux-x86_64/VERSION", "0.1.0")
        handle.writestr("dontneed-dns-linux-x86_64/src/dontneed_dns/__init__.py", "x")
        handle.writestr("dontneed-dns-linux-x86_64/install.sh", "x")
        handle.writestr("dontneed-dns-linux-x86_64/uninstall.sh", "x")
    build_release.verify_archive(archive, "linux")


def test_a_windows_archive_without_the_executable_is_rejected(tmp_path: Path) -> None:
    """The end user could not install an archive without the executable."""
    archive = tmp_path / "dontneed-dns-windows-x86_64.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("dontneed-dns-windows-x86_64/README.md", "x")
        handle.writestr("dontneed-dns-windows-x86_64/LICENSE", "x")
        handle.writestr("dontneed-dns-windows-x86_64/VERSION", "0.1.0")
        handle.writestr("dontneed-dns-windows-x86_64/src/dontneed_dns/__init__.py", "x")
        handle.writestr("dontneed-dns-windows-x86_64/install.ps1", "x")
        handle.writestr("dontneed-dns-windows-x86_64/uninstall.ps1", "x")
    with pytest.raises(SystemExit, match="missing"):
        build_release.verify_archive(archive, "windows")


def test_a_complete_windows_archive_is_accepted(tmp_path: Path) -> None:
    archive = tmp_path / "dontneed-dns-windows-x86_64.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("dontneed-dns-windows-x86_64/README.md", "x")
        handle.writestr("dontneed-dns-windows-x86_64/LICENSE", "x")
        handle.writestr("dontneed-dns-windows-x86_64/VERSION", "0.1.0")
        handle.writestr("dontneed-dns-windows-x86_64/src/dontneed_dns/__init__.py", "x")
        handle.writestr("dontneed-dns-windows-x86_64/install.ps1", "x")
        handle.writestr("dontneed-dns-windows-x86_64/uninstall.ps1", "x")
        handle.writestr("dontneed-dns-windows-x86_64/dontneed-dns.exe", "x")
    build_release.verify_archive(archive, "windows")


def test_the_checksums_are_written(tmp_path: Path) -> None:
    first = tmp_path / "a.zip"
    second = tmp_path / "b.zip"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    checksum_file = build_release.write_checksums([first, second], tmp_path)
    content = checksum_file.read_text(encoding="utf-8")
    assert len(content.strip().splitlines()) == 2
    assert "a.zip" in content
    assert build_release.sha256_of(first) in content


def test_the_version_is_read_from_the_project() -> None:
    from dontneed_dns import __version__

    assert build_release.read_version() == __version__


# -- the verification of a built release -------------------------------------


def _write_zip(path: Path, entries: dict[str, str]) -> None:
    """Write a zip file with the given entries."""
    with zipfile.ZipFile(path, "w") as handle:
        for name, content in entries.items():
            handle.writestr(name, content)


def test_the_verifier_accepts_a_complete_windows_archive(tmp_path: Path) -> None:
    import verify_release

    archive = tmp_path / "dontneed-dns-windows-x86_64.zip"
    _write_zip(
        archive,
        {
            "dontneed-dns-windows-x86_64/README.md": "x",
            "dontneed-dns-windows-x86_64/LICENSE": "x",
            "dontneed-dns-windows-x86_64/VERSION": "0.1.0",
            "dontneed-dns-windows-x86_64/src/dontneed_dns/__init__.py": "x",
            "dontneed-dns-windows-x86_64/install.ps1": "x",
            "dontneed-dns-windows-x86_64/uninstall.ps1": "x",
            "dontneed-dns-windows-x86_64/dontneed-dns.exe": "x",
        },
    )
    assert verify_release.verify_archive(archive) == []


def test_the_verifier_rejects_a_windows_archive_without_the_executable(tmp_path: Path) -> None:
    """The end user could not install an archive without the executable."""
    import verify_release

    archive = tmp_path / "dontneed-dns-windows-x86_64.zip"
    _write_zip(
        archive,
        {
            "dontneed-dns-windows-x86_64/README.md": "x",
            "dontneed-dns-windows-x86_64/LICENSE": "x",
            "dontneed-dns-windows-x86_64/VERSION": "0.1.0",
            "dontneed-dns-windows-x86_64/src/dontneed_dns/__init__.py": "x",
            "dontneed-dns-windows-x86_64/install.ps1": "x",
            "dontneed-dns-windows-x86_64/uninstall.ps1": "x",
        },
    )
    problems = verify_release.verify_archive(archive)
    assert any("dontneed-dns.exe" in problem for problem in problems)


def test_a_published_file_without_a_checksum_is_reported(tmp_path: Path) -> None:
    """Every published file has to be listed in the checksum file.

    A checksum file that was written with a fallback would leave the
    executable unprotected, and the user could not check it.
    """
    import verify_release

    archive = tmp_path / "dontneed-dns-linux-x86_64.zip"
    archive.write_bytes(b"one")
    executable = tmp_path / "dontneed-dns.exe"
    executable.write_bytes(b"two")
    checksum_file = tmp_path / "SHA256SUMS"
    checksum_file.write_text(f"{build_release.sha256_of(archive)}  {archive.name}\n", encoding="utf-8")

    problems = verify_release.verify_checksums(tmp_path)
    assert any("dontneed-dns.exe" in problem for problem in problems)


def test_a_listed_file_that_does_not_exist_is_reported(tmp_path: Path) -> None:
    import verify_release

    checksum_file = tmp_path / "SHA256SUMS"
    checksum_file.write_text("0" * 64 + "  missing.zip\n", encoding="utf-8")

    problems = verify_release.verify_checksums(tmp_path)
    assert any("missing.zip" in problem for problem in problems)


def test_the_checksums_of_a_complete_release_are_accepted(tmp_path: Path) -> None:
    import verify_release

    archive = tmp_path / "dontneed-dns-linux-x86_64.zip"
    archive.write_bytes(b"one")
    executable = tmp_path / "dontneed-dns.exe"
    executable.write_bytes(b"two")
    checksum_file = tmp_path / "SHA256SUMS"
    checksum_file.write_text(
        f"{build_release.sha256_of(archive)}  {archive.name}\n"
        f"{build_release.sha256_of(executable)}  {executable.name}\n",
        encoding="utf-8",
    )

    assert verify_release.verify_checksums(tmp_path) == []


def test_the_installer_scripts_are_syntactically_valid() -> None:
    """The shell scripts must at least be parseable by the shell."""
    import shutil
    import subprocess

    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is not available")

    for name in ("install.sh", "uninstall.sh"):
        path = build_release.PACKAGING_DIR / "linux" / name
        completed = subprocess.run(
            [bash, "-n", str(path)], capture_output=True, text=True, errors="replace", check=False
        )
        assert completed.returncode == 0, completed.stderr
