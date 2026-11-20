"""Integration tests of the self contained installation.

The privileged operations are mocked: the tests verify the files that are
written, the unit files that are rendered and the order of the calls.  They
never touch the services of the machine that runs them.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from dontneed_dns.core.system import CommandResult
from dontneed_dns.installer import Installer
from dontneed_dns.platforms import linux

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not sys.platform.startswith("linux"),
        reason="the Fedora edition is only tested on Linux",
    ),
]


#: The installation registers the systemd user services, so a running
#: systemd is needed.
systemd_required = pytest.mark.skipif(
    not Path("/run/systemd/system").exists(),
    reason="the installation of the services needs a running systemd",
)


class FakeSystemctl:
    """Records every systemctl call and answers plausibly."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv, *, timeout=30.0, **kwargs):
        self.calls.append(list(argv))
        joined = " ".join(argv)
        if "show" in joined and "LoadState" in joined:
            return CommandResult(tuple(argv), 0, "not-found\n", "")
        if "is-active" in joined:
            return CommandResult(tuple(argv), 3, "inactive\n", "")
        if "is-enabled" in joined:
            return CommandResult(tuple(argv), 0, "enabled\n", "")
        return CommandResult(tuple(argv), 0, "", "")

    def called(self, needle: str) -> bool:
        return any(needle in " ".join(call) for call in self.calls)


@pytest.fixture
def systemctl(monkeypatch: pytest.MonkeyPatch) -> FakeSystemctl:
    fake = FakeSystemctl()
    monkeypatch.setattr(linux.adapter, "run_command", fake)
    return fake


@pytest.fixture
def unit_dir(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Path:
    directory = tmp_path / "systemd" / "user"
    directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(linux.adapter, "SYSTEMD_USER_UNIT_DIR", directory)
    return directory


@pytest.fixture
def installer(tmp_path: Path, unit_dir: Path) -> Installer:
    payload = tmp_path / "payload"
    payload.write_bytes(b"#!/bin/sh\nexit 0\n")
    payload.chmod(0o755)
    instance = Installer(
        config_path=tmp_path / "config.toml",
        payload_source=payload,
        target_binary=tmp_path / "install" / "bin" / "dontneed-dns",
        health_runner=lambda config: [],
        startup_wait=0.0,
        # The transparent router needs the administrator rights; it has its
        # own tests.
        transparent=False,
    )
    instance.frozen = True
    return instance


@systemd_required
def test_the_installation_writes_the_application_the_units_and_the_configuration(
    installer, tmp_path, systemctl
) -> None:
    assert installer.install() == 0

    binary = tmp_path / "install" / "bin" / "dontneed-dns"
    assert binary.exists()

    config = tmp_path / "config.toml"
    assert config.exists()
    assert "split_domains" in config.read_text(encoding="utf-8")

    unit = tmp_path / "systemd" / "user" / "dontneed-dns-dns.service"
    assert unit.exists()
    content = unit.read_text(encoding="utf-8")
    assert "ExecStart=" in content
    assert f"{binary} run dns --config {config}" in content
    assert "NoNewPrivileges=yes" in content
    assert "ProtectSystem=strict" in content

    proxy_unit = tmp_path / "systemd" / "user" / "dontneed-dns-proxy.service"
    assert proxy_unit.exists()
    assert f"{binary} run proxy --config {config}" in proxy_unit.read_text(encoding="utf-8")


@systemd_required
def test_the_services_are_enabled_and_started(installer, systemctl) -> None:
    assert installer.install() == 0
    assert systemctl.called("enable")
    assert systemctl.called("start")
    assert systemctl.called("daemon-reload")


@systemd_required
def test_the_automatic_start_follows_the_configuration(installer, systemctl) -> None:
    installer.install()
    assert systemctl.called("enable")

    systemctl.calls.clear()
    installer.target_config.write_text("[service]\nautostart = false\n", encoding="utf-8")
    installer.install()
    assert systemctl.called("disable")


@systemd_required
def test_the_uninstallation_removes_only_what_the_project_owns(installer, tmp_path, systemctl) -> None:
    installer.install()
    binary = tmp_path / "install" / "bin" / "dontneed-dns"
    config = tmp_path / "config.toml"
    unit = tmp_path / "systemd" / "user" / "dontneed-dns-dns.service"

    assert installer.uninstall() == 0
    assert not binary.exists()
    assert not unit.exists()
    assert config.exists(), "the configuration must be kept without --purge"


@systemd_required
def test_the_uninstallation_can_remove_the_configuration(installer, tmp_path) -> None:
    installer.install()
    config = tmp_path / "config.toml"
    assert config.exists()
    assert installer.uninstall(purge=True) == 0
    assert not config.exists()


@systemd_required
def test_a_reinstallation_works(installer, tmp_path, systemctl) -> None:
    assert installer.install() == 0
    binary = tmp_path / "install" / "bin" / "dontneed-dns"
    first = binary.read_bytes()
    binary.write_bytes(b"broken")
    assert installer.install() == 0
    assert binary.read_bytes() == first
