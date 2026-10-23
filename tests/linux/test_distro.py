"""Tests of the distribution detection."""

from __future__ import annotations

from pathlib import Path

from dontneed_dns.platforms.linux.distro import detect_distro


def test_cachyos_is_detected(tmp_path: Path) -> None:
    release = tmp_path / "os-release"
    release.write_text(
        'NAME="CachyOS Linux"\nID=cachyos\nID_LIKE="arch"\nVERSION_ID="260513"\n',
        encoding="utf-8",
    )
    info = detect_distro(release)
    assert info.identifier == "cachyos"
    assert info.family == "arch"
    assert info.is_known is True


def test_ubuntu_is_detected(tmp_path: Path) -> None:
    release = tmp_path / "os-release"
    release.write_text('NAME="Ubuntu"\nID=ubuntu\nID_LIKE="debian"\n', encoding="utf-8")
    info = detect_distro(release)
    assert info.identifier == "ubuntu"
    assert info.family == "debian"
    assert info.is_known is True


def test_fedora_is_detected(tmp_path: Path) -> None:
    release = tmp_path / "os-release"
    release.write_text('NAME="Fedora Linux"\nID=fedora\nVERSION_ID="41"\n', encoding="utf-8")
    info = detect_distro(release)
    assert info.family == "fedora"
    assert info.is_known is True


def test_an_unknown_distribution_is_not_known(tmp_path: Path) -> None:
    release = tmp_path / "os-release"
    release.write_text('NAME="Something"\nID=something\n', encoding="utf-8")
    info = detect_distro(release)
    assert info.is_known is False


def test_a_missing_file_yields_an_unknown_distribution(tmp_path: Path) -> None:
    info = detect_distro(tmp_path / "missing")
    assert info.identifier == "unknown"
    assert info.is_known is False


def test_comments_are_ignored(tmp_path: Path) -> None:
    release = tmp_path / "os-release"
    release.write_text("# a comment\n\nID=debian\n", encoding="utf-8")
    info = detect_distro(release)
    assert info.identifier == "debian"
