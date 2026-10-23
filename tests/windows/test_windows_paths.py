"""Tests of the Windows path helpers and of the scheduled task backend."""

from __future__ import annotations

from pathlib import Path

import pytest

from dontneed_dns.core.system import CommandResult
from dontneed_dns.platforms.windows import paths as win_paths
from dontneed_dns.platforms.windows.scheduled_task import ScheduledTaskBackend

pytestmark = pytest.mark.windows


def test_the_configuration_lives_in_the_roaming_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPDATA", r"C:\Users\tester\AppData\Roaming")
    assert win_paths.config_dir() == Path(r"C:\Users\tester\AppData\Roaming") / "dontneed-dns"
    assert win_paths.config_path().name == "config.toml"


def test_the_logs_live_in_the_local_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\tester\AppData\Local")
    assert win_paths.log_dir() == Path(r"C:\Users\tester\AppData\Local") / "dontneed-dns" / "logs"


def test_the_state_lives_in_the_local_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\tester\AppData\Local")
    assert win_paths.state_dir() == Path(r"C:\Users\tester\AppData\Local") / "dontneed-dns" / "state"


def test_the_program_data_directory_is_used_for_system_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROGRAMDATA", r"C:\ProgramData")
    assert win_paths.program_data() == Path(r"C:\ProgramData")


def test_the_paths_fall_back_to_the_home_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setenv("HOME", "/tmp/tester")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/tmp/tester")))
    assert win_paths.local_app_data() == Path("/tmp/tester") / "AppData" / "Local"


def test_the_task_name_is_derived_from_the_component() -> None:
    backend = ScheduledTaskBackend()
    assert backend.task_name("proxy") == "dontneed-dns-proxy"
    assert backend.task_name("dns") == "dontneed-dns-dns"


class FakeRunner:
    """Records the calls to ``schtasks``."""

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


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch) -> FakeRunner:
    from dontneed_dns.platforms.windows import scheduled_task as module

    fake = FakeRunner()
    monkeypatch.setattr(module, "run_command", fake)
    return fake


def test_the_task_is_created_with_the_expected_arguments(runner) -> None:
    backend = ScheduledTaskBackend()
    backend.create("dontneed-dns-dns", r'"C:\app\dontneed-dns.exe" run dns')
    call = runner.calls[-1]
    assert call[0] == "schtasks"
    assert "/Create" in call
    assert "/TN" in call
    assert "dontneed-dns-dns" in call
    assert "/SC" in call
    assert "ONLOGON" in call


def test_the_task_is_run_and_ended(runner) -> None:
    backend = ScheduledTaskBackend()
    backend.run("x")
    assert "/Run" in runner.calls[-1]
    backend.end("x")
    assert "/End" in runner.calls[-1]


def test_the_task_is_deleted(runner) -> None:
    backend = ScheduledTaskBackend()
    backend.delete("x")
    assert "/Delete" in runner.calls[-1]
    assert "/F" in runner.calls[-1]


def test_the_task_is_enabled_and_disabled(runner) -> None:
    backend = ScheduledTaskBackend()
    backend.enable("x")
    assert "/ENABLE" in runner.calls[-1]
    backend.disable("x")
    assert "/DISABLE" in runner.calls[-1]


def test_a_missing_task_is_reported(runner) -> None:
    runner.answers["/Query"] = CommandResult(("",), 1, "", "not found")
    backend = ScheduledTaskBackend()
    assert backend.exists("x") is False


def test_the_enabled_state_is_read_from_the_xml(runner) -> None:
    xml = "<Task><Settings><Enabled>true</Enabled></Settings></Task>"
    runner.answers["/XML"] = CommandResult(("",), 0, xml, "")
    backend = ScheduledTaskBackend()
    assert backend.is_enabled("x") is True


def test_broken_xml_yields_an_unknown_state(runner) -> None:
    runner.answers["/XML"] = CommandResult(("",), 0, "<not xml", "")
    backend = ScheduledTaskBackend()
    assert backend.is_enabled("x") is None


def test_the_pid_file_is_read(runner, tmp_path: Path) -> None:
    backend = ScheduledTaskBackend()
    pid_file = tmp_path / "x.pid"
    pid_file.write_text("4321", encoding="utf-8")
    assert backend.read_pid(pid_file) == 4321


def test_a_broken_pid_file_is_ignored(runner, tmp_path: Path) -> None:
    backend = ScheduledTaskBackend()
    pid_file = tmp_path / "x.pid"
    pid_file.write_text("not a number", encoding="utf-8")
    assert backend.read_pid(pid_file) is None


def test_a_missing_pid_file_is_ignored(runner, tmp_path: Path) -> None:
    backend = ScheduledTaskBackend()
    assert backend.read_pid(tmp_path / "missing.pid") is None


def test_the_availability_check_uses_schtasks(monkeypatch: pytest.MonkeyPatch) -> None:
    from dontneed_dns.platforms.windows import scheduled_task as module

    monkeypatch.setattr(module, "which", lambda name: r"C:\Windows\System32\schtasks.exe")
    backend = ScheduledTaskBackend()
    assert backend.is_available() is True

    monkeypatch.setattr(module, "which", lambda name: None)
    assert backend.is_available() is False
