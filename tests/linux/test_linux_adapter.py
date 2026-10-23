"""Tests of the Linux edition.

The privileged operations are mocked; the tests therefore run everywhere and
verify that the adapter builds the right command lines and reacts correctly
to every failure.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from dontneed_dns.core.system import CommandResult
from dontneed_dns.platforms import linux
from dontneed_dns.platforms.base import Component, ServiceState
from dontneed_dns.platforms.linux.adapter import LinuxPlatformAdapter

pytestmark = [
    pytest.mark.linux,
    pytest.mark.skipif(
        not sys.platform.startswith("linux"),
        reason="the Linux edition is only tested on Linux",
    ),
]


class FakeSystemctl:
    """Records the calls and answers with scripted results."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.answers: dict[str, CommandResult] = {}
        self.default = CommandResult(("",), 0, "", "")

    def __call__(self, argv, *, timeout=30.0, **kwargs):
        self.calls.append(list(argv))
        for needle, result in self.answers.items():
            if needle in " ".join(argv):
                return result
        return self.default

    def called(self, needle: str) -> bool:
        """``True`` when a call contains ``needle``."""
        return any(needle in " ".join(call) for call in self.calls)


@pytest.fixture
def systemctl(monkeypatch: pytest.MonkeyPatch) -> FakeSystemctl:
    fake = FakeSystemctl()
    monkeypatch.setattr(linux.adapter, "run_command", fake)
    return fake


@pytest.fixture
def adapter() -> LinuxPlatformAdapter:
    return LinuxPlatformAdapter()


def test_the_adapter_names_itself_linux(adapter: LinuxPlatformAdapter) -> None:
    assert adapter.name == "linux"


def test_the_capability_matrix_is_the_linux_one(adapter: LinuxPlatformAdapter) -> None:
    assert adapter.capabilities().platform == "linux"


def test_a_running_unit_is_reported_as_running(adapter, systemctl) -> None:
    systemctl.answers["LoadState"] = CommandResult(("",), 0, "loaded\n", "")
    systemctl.answers["is-active"] = CommandResult(("",), 0, "active\n", "")
    assert adapter.status(Component.DNS) is ServiceState.RUNNING


def test_a_stopped_unit_is_reported_as_stopped(adapter, systemctl) -> None:
    systemctl.answers["LoadState"] = CommandResult(("",), 0, "loaded\n", "")
    systemctl.answers["is-active"] = CommandResult(("",), 3, "inactive\n", "")
    assert adapter.status(Component.DNS) is ServiceState.STOPPED


def test_a_failed_unit_is_reported_as_failed(adapter, systemctl) -> None:
    systemctl.answers["LoadState"] = CommandResult(("",), 0, "loaded\n", "")
    systemctl.answers["is-active"] = CommandResult(("",), 3, "failed\n", "")
    assert adapter.status(Component.PROXY) is ServiceState.FAILED


def test_a_missing_unit_is_reported_as_not_installed(adapter, systemctl) -> None:
    systemctl.answers["LoadState"] = CommandResult(("",), 0, "not-found\n", "")
    assert adapter.status(Component.DNS) is ServiceState.NOT_INSTALLED


def test_the_user_scope_uses_systemctl_user(adapter, systemctl) -> None:
    adapter.scope = "user"
    adapter.start(Component.DNS)
    assert systemctl.calls
    assert "--user" in systemctl.calls[-1]


def test_the_system_scope_does_not_use_systemctl_user(adapter, systemctl) -> None:
    adapter.scope = "system"
    adapter.start(Component.DNS)
    assert "--user" not in systemctl.calls[-1]


def test_start_uses_the_right_unit_name(adapter, systemctl) -> None:
    adapter.start(Component.DNS)
    assert "dontneed-dns-dns.service" in systemctl.calls[-1]

    adapter.start(Component.PROXY)
    assert "dontneed-dns-proxy.service" in systemctl.calls[-1]


def test_a_failing_start_is_reported(adapter, systemctl) -> None:
    systemctl.default = CommandResult(("",), 1, "", "Unit not found")
    result = adapter.start(Component.DNS)
    assert not result.ok
    assert "failed" in result.message


def test_the_autostart_state_is_read(adapter, systemctl) -> None:
    systemctl.answers["is-enabled"] = CommandResult(("",), 0, "enabled\n", "")
    assert adapter.is_enabled(Component.DNS) is True

    systemctl.answers["is-enabled"] = CommandResult(("",), 1, "disabled\n", "")
    assert adapter.is_enabled(Component.DNS) is False

    systemctl.answers["is-enabled"] = CommandResult(("",), 1, "", "not found")
    assert adapter.is_enabled(Component.DNS) is None


def test_enable_and_disable_call_systemctl(adapter, systemctl) -> None:
    adapter.enable(Component.DNS)
    assert "enable" in systemctl.calls[-1]

    adapter.disable(Component.DNS)
    assert "disable" in systemctl.calls[-1]


def test_the_logs_are_read_from_the_journal(adapter, systemctl) -> None:
    systemctl.answers["journalctl"] = CommandResult(("",), 0, "line one\nline two\n", "")
    result = adapter.logs(Component.DNS, lines=10)
    assert result.ok
    assert "line one" in result.data["output"]
    assert "journalctl" in systemctl.calls[-1]


def test_install_writes_the_unit_file(adapter, systemctl, tmp_path, monkeypatch) -> None:
    unit_dir = tmp_path / "systemd" / "user"
    monkeypatch.setattr(linux.adapter, "SYSTEMD_USER_UNIT_DIR", unit_dir)

    result = adapter.install(Component.DNS, scope="user")
    assert result.ok

    unit = unit_dir / "dontneed-dns-dns.service"
    assert unit.exists()
    content = unit.read_text()
    assert "ExecStart=" in content
    assert "dontneed_dns.service_runner dns" in content
    assert systemctl.called("daemon-reload")
    assert systemctl.called("enable")


def test_install_is_idempotent(adapter, systemctl, tmp_path, monkeypatch) -> None:
    unit_dir = tmp_path / "systemd" / "user"
    monkeypatch.setattr(linux.adapter, "SYSTEMD_USER_UNIT_DIR", unit_dir)

    adapter.install(Component.DNS, scope="user")
    first = (unit_dir / "dontneed-dns-dns.service").read_text()
    adapter.install(Component.DNS, scope="user")
    second = (unit_dir / "dontneed-dns-dns.service").read_text()

    assert first == second


def test_the_unit_file_names_the_right_component(adapter, systemctl, tmp_path, monkeypatch) -> None:
    unit_dir = tmp_path / "systemd" / "user"
    monkeypatch.setattr(linux.adapter, "SYSTEMD_USER_UNIT_DIR", unit_dir)

    adapter.install(Component.PROXY, scope="user")
    content = (unit_dir / "dontneed-dns-proxy.service").read_text()
    assert "dontneed_dns.service_runner proxy" in content


def test_uninstall_removes_the_unit_file(adapter, systemctl, tmp_path, monkeypatch) -> None:
    unit_dir = tmp_path / "systemd" / "user"
    monkeypatch.setattr(linux.adapter, "SYSTEMD_USER_UNIT_DIR", unit_dir)

    adapter.install(Component.DNS, scope="user")
    result = adapter.uninstall(Component.DNS, scope="user")

    assert result.ok
    assert not (unit_dir / "dontneed-dns-dns.service").exists()
    assert systemctl.called("disable --now")


def test_uninstall_does_not_touch_the_other_component(adapter, systemctl, tmp_path, monkeypatch) -> None:
    unit_dir = tmp_path / "systemd" / "user"
    monkeypatch.setattr(linux.adapter, "SYSTEMD_USER_UNIT_DIR", unit_dir)

    adapter.install(Component.DNS, scope="user")
    adapter.install(Component.PROXY, scope="user")
    adapter.uninstall(Component.DNS, scope="user")

    assert not (unit_dir / "dontneed-dns-dns.service").exists()
    assert (unit_dir / "dontneed-dns-proxy.service").exists()


def test_a_system_scope_installation_without_root_asks_for_elevation(adapter, systemctl, monkeypatch) -> None:
    monkeypatch.setattr(linux.adapter, "is_root", lambda: False)
    result = adapter.install(Component.DNS, scope="system")
    assert not result.ok
    assert result.requires_elevation


def test_a_system_scope_uninstallation_without_root_asks_for_elevation(
    adapter, systemctl, monkeypatch
) -> None:
    monkeypatch.setattr(linux.adapter, "is_root", lambda: False)
    result = adapter.uninstall(Component.DNS, scope="system")
    assert not result.ok
    assert result.requires_elevation


def test_the_diagnostics_do_not_leak_secrets(adapter) -> None:
    data = adapter.diagnostics()
    assert data["platform"] == "linux"
    assert set(data["components"]) == {"dns", "proxy"}
    for component in data["components"].values():
        assert "state" in component
        assert "unit" in component


def test_the_install_paths_are_reported(adapter) -> None:
    paths = adapter.install_paths("user")
    assert "config" in paths
    assert "dns_unit" in paths
    assert paths["dns_unit"].name == "dontneed-dns-dns.service"


def test_the_unit_directory_follows_xdg_config_home(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """systemd reads the user units from ``$XDG_CONFIG_HOME``."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert linux.adapter._user_unit_dir() == tmp_path / "systemd" / "user"


def test_the_unit_directory_falls_back_to_the_home_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert linux.adapter._user_unit_dir() == tmp_path / ".config" / "systemd" / "user"
