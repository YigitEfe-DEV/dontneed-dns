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
    build_release.verify_archive(archive, "linux")


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


def test_the_installer_scripts_are_syntactically_valid() -> None:
    """The shell scripts must at least be parseable by the shell."""
    import shutil
    import subprocess

    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is not available")

    for name in ("install.sh", "uninstall.sh"):
        path = build_release.PACKAGING_DIR / "linux" / name
        completed = subprocess.run([bash, "-n", str(path)], capture_output=True, text=True, check=False)
        assert completed.returncode == 0, completed.stderr
