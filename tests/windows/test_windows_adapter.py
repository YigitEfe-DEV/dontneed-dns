"""Tests of the Windows edition.

The external commands are mocked so that the behaviour of the adapter is
verified on every platform; the same tests run unchanged on the Windows
runner of the GitHub Actions workflow.
"""

from __future__ import annotations

import pytest

from dontneed_dns.core.system import CommandResult
from dontneed_dns.platforms.base import Component, ServiceState
from dontneed_dns.platforms.windows import adapter as windows_adapter
from dontneed_dns.platforms.windows import scheduled_task as task_module
from dontneed_dns.platforms.windows import scm as windows_scm

pytestmark = pytest.mark.windows


class FakeSchtasks:
    """Records the calls and answers with scripted results."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.answers: dict[str, CommandResult] = {}
        self.default = CommandResult(("",), 0, "", "")

    def __call__(self, argv, *, timeout=60.0, **kwargs):
        self.calls.append(list(argv))
        for needle, result in self.answers.items():
            if needle in " ".join(argv):
                return result
        return self.default

    def called(self, needle: str) -> bool:
        """``True`` when a call contains ``needle``."""
        return any(needle in " ".join(call) for call in self.calls)


TASK_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Settings>
    <Enabled>true</Enabled>
  </Settings>
</Task>
"""


@pytest.fixture
def schtasks(monkeypatch: pytest.MonkeyPatch) -> FakeSchtasks:
    fake = FakeSchtasks()
    monkeypatch.setattr(task_module, "run_command", fake)
    return fake


@pytest.fixture
def adapter() -> windows_adapter.WindowsPlatformAdapter:
    return windows_adapter.WindowsPlatformAdapter()


def test_the_adapter_names_itself_windows(adapter) -> None:
    assert adapter.name == "windows"


def test_the_capability_matrix_is_the_windows_one(adapter) -> None:
    assert adapter.capabilities().platform == "windows"


def test_a_missing_task_is_reported_as_not_installed(adapter, schtasks) -> None:
    schtasks.answers["/Query"] = CommandResult(("",), 1, "", "ERROR: The system cannot find the file.")
    assert adapter.status(Component.DNS) is ServiceState.NOT_INSTALLED


def test_an_existing_but_stopped_task_is_reported_as_stopped(adapter, schtasks, tmp_path) -> None:
    schtasks.answers["/Query"] = CommandResult(("",), 0, "TaskName: x", "")
    schtasks.answers["/XML"] = CommandResult(("",), 0, TASK_XML, "")
    adapter._pid_path = lambda component: tmp_path / "missing.pid"  # type: ignore[method-assign]
    assert adapter.status(Component.DNS) is ServiceState.STOPPED


def test_a_running_task_is_reported_as_running(adapter, schtasks, tmp_path) -> None:
    import os

    schtasks.answers["/Query"] = CommandResult(("",), 0, "TaskName: x", "")
    schtasks.answers["/XML"] = CommandResult(("",), 0, TASK_XML, "")
    pid_file = tmp_path / "proxy.pid"
    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    adapter._pid_path = lambda component: pid_file  # type: ignore[method-assign]
    assert adapter.status(Component.PROXY) is ServiceState.RUNNING


def test_the_autostart_state_is_read_from_the_task_xml(adapter, schtasks) -> None:
    schtasks.answers["/XML"] = CommandResult(("",), 0, TASK_XML, "")
    assert adapter.is_enabled(Component.DNS) is True

    disabled = TASK_XML.replace("<Enabled>true</Enabled>", "<Enabled>false</Enabled>")
    schtasks.answers["/XML"] = CommandResult(("",), 0, disabled, "")
    assert adapter.is_enabled(Component.DNS) is False


def test_start_runs_the_task(adapter, schtasks) -> None:
    result = adapter.start(Component.DNS)
    assert result.ok
    assert schtasks.called("/Run")


def test_start_fails_when_the_task_is_missing(adapter, schtasks) -> None:
    schtasks.answers["/Query"] = CommandResult(("",), 1, "", "not found")
    result = adapter.start(Component.DNS)
    assert not result.ok


def test_stop_ends_the_task(adapter, schtasks) -> None:
    result = adapter.stop(Component.DNS)
    assert result.ok
    assert schtasks.called("/End")


def test_enable_and_disable_change_the_task(adapter, schtasks) -> None:
    adapter.enable(Component.DNS)
    assert schtasks.called("/ENABLE")

    adapter.disable(Component.DNS)
    assert schtasks.called("/DISABLE")


def test_install_creates_the_task(adapter, schtasks, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(windows_adapter.win_paths, "config_path", lambda: tmp_path / "config.toml")
    result = adapter.install(Component.DNS, scope="user")
    assert result.ok
    assert schtasks.called("/Create")
    assert (tmp_path / "config.toml").exists()
    assert "dns" in " ".join(schtasks.calls[-1])


def test_install_is_idempotent(adapter, schtasks, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(windows_adapter.win_paths, "config_path", lambda: tmp_path / "config.toml")
    adapter.install(Component.DNS, scope="user")
    adapter.install(Component.DNS, scope="user")
    assert schtasks.called("/Create")


def test_uninstall_removes_the_task(adapter, schtasks) -> None:
    result = adapter.uninstall(Component.DNS, scope="user")
    assert result.ok
    assert schtasks.called("/Delete")


def test_a_system_scope_installation_asks_for_elevation(adapter, schtasks, monkeypatch) -> None:
    monkeypatch.setattr(windows_adapter, "_is_admin", lambda: False)
    result = adapter.install(Component.DNS, scope="system")
    assert not result.ok
    assert result.requires_elevation


def test_a_system_scope_uninstallation_asks_for_elevation(adapter, schtasks, monkeypatch) -> None:
    monkeypatch.setattr(windows_adapter, "_is_admin", lambda: False)
    result = adapter.uninstall(Component.DNS, scope="system")
    assert not result.ok
    assert result.requires_elevation


def test_the_service_backend_reports_missing_pywin32(adapter, monkeypatch) -> None:
    monkeypatch.setattr(windows_scm, "pywin32_available", lambda: False)
    monkeypatch.setattr(windows_adapter, "_is_admin", lambda: True)
    result = adapter.install(Component.DNS, scope="system")
    assert not result.ok
    assert result.unsupported


def test_the_windows_service_backend_is_used_when_it_is_available(adapter, monkeypatch) -> None:
    created: list[str] = []

    def fake_create(service_name, binary_path, *, display_name):
        created.append(service_name)
        return CommandResult(("",), 0, "", "")

    monkeypatch.setattr(adapter.scm, "is_available", lambda: True)
    monkeypatch.setattr(adapter.scm, "exists", lambda name: False)
    monkeypatch.setattr(adapter.scm, "create", fake_create)
    monkeypatch.setattr(windows_adapter, "_is_admin", lambda: True)

    result = adapter.install(Component.DNS, scope="system")
    assert result.ok
    assert created == ["dontneed-dns-dns"]


def test_the_logs_are_read_from_the_log_file(adapter, tmp_path, monkeypatch) -> None:
    log_file = tmp_path / "dontneed-dns.log"
    log_file.write_text("first\nsecond\nthird\n", encoding="utf-8")
    monkeypatch.setattr(windows_adapter.win_paths, "log_path", lambda: log_file)

    result = adapter.logs(Component.DNS, lines=2)
    assert result.ok
    output = result.data["output"]
    assert "second" in output
    assert "first" not in output


def test_a_missing_log_file_is_not_an_error(adapter, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(windows_adapter.win_paths, "log_path", lambda: tmp_path / "missing.log")
    result = adapter.logs(Component.DNS)
    assert result.ok
    assert result.data["output"] == ""


def test_the_diagnostics_describe_the_installation(adapter) -> None:
    data = adapter.diagnostics()
    assert data["platform"] == "windows"
    assert data["backend"] == "scheduled-task"
    assert set(data["components"]) == {"dns", "proxy"}


def test_the_install_paths_are_reported(adapter) -> None:
    paths = adapter.install_paths("user")
    assert "config" in paths
    assert "logs" in paths
    assert "state" in paths


def test_the_frozen_executable_uses_the_run_command(adapter, monkeypatch) -> None:
    monkeypatch.setattr(windows_adapter.sys, "frozen", True, raising=False)
    monkeypatch.setattr(windows_adapter.sys, "executable", r"C:\Program Files\dontneed-dns\dontneed-dns.exe")
    command = adapter._command_line(Component.PROXY)
    assert "run proxy" in command
    assert ".exe" in command


def test_the_source_installation_uses_the_module(adapter) -> None:
    command = adapter._command_line(Component.DNS)
    assert "dontneed_dns.service_runner dns" in command


def test_an_unknown_scope_is_rejected() -> None:
    with pytest.raises(ValueError):
        windows_adapter.WindowsPlatformAdapter(scope="root")


def test_an_unknown_backend_is_rejected() -> None:
    with pytest.raises(ValueError):
        windows_adapter.WindowsPlatformAdapter(backend="magic")


def test_the_windows_paths_are_under_the_application_data(adapter) -> None:
    paths = adapter.install_paths("user")
    assert str(paths["config"]).endswith("config.toml")
    assert "dontneed-dns" in str(paths["logs"])
