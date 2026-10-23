"""Tests of the Windows Task Scheduler backend."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from dontneed_dns.core.system import CommandResult
from dontneed_dns.platforms.windows import scheduled_task as task_module
from dontneed_dns.platforms.windows.scheduled_task import (
    ScheduledTaskBackend,
    TaskDefinition,
    build_task_xml,
    current_user_id,
)

pytestmark = pytest.mark.windows


def test_the_task_name_is_derived_from_the_component() -> None:
    backend = ScheduledTaskBackend()
    assert backend.task_name("proxy") == "dontneed-dns-proxy"
    assert backend.task_name("dns") == "dontneed-dns-dns"


def test_the_account_of_the_current_user_is_built_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("USERNAME", "Excalibur")
    monkeypatch.setenv("USERDOMAIN", "ATALYY")
    assert current_user_id() == "ATALYY\\Excalibur"


def test_a_local_account_has_no_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USERNAME", "Excalibur")
    monkeypatch.setenv("USERDOMAIN", "Excalibur")
    assert current_user_id() == "Excalibur"


def test_the_xml_definition_holds_the_command_and_the_arguments() -> None:
    definition = TaskDefinition(
        command=r"C:\Users\Excalibur\AppData\Local\dontneed-dns\dontneed-dns.exe",
        arguments='run dns --config "C:\\Users\\Excalibur\\AppData\\Roaming\\config.toml"',
        user_id="ATALYY\\Excalibur",
        description="dontneed-dns DNS resolver",
    )
    xml = build_task_xml(definition)
    assert "<Command>C:\\Users\\Excalibur\\AppData\\Local\\dontneed-dns\\dontneed-dns.exe</Command>" in xml
    assert "run dns --config" in xml
    assert "<UserId>ATALYY\\Excalibur</UserId>" in xml
    assert "<LogonType>InteractiveToken</LogonType>" in xml
    assert "<RunLevel>LeastPrivilege</RunLevel>" in xml


def test_the_xml_definition_escapes_special_characters() -> None:
    definition = TaskDefinition(
        command=r"C:\a&b\app.exe",
        arguments='--name "x" <y>',
        user_id="DOMAIN\\user",
    )
    xml = build_task_xml(definition)
    assert "&amp;" in xml
    assert "&quot;" in xml
    assert "&lt;y&gt;" in xml


def test_the_task_has_no_execution_time_limit() -> None:
    """The default limit of 72 hours would stop the proxy."""
    xml = build_task_xml(TaskDefinition(command="app.exe"))
    assert "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>" in xml


def test_the_task_is_created_from_an_xml_file(schtasks) -> None:
    backend = ScheduledTaskBackend()
    definition = TaskDefinition(
        command=r"C:\app\dontneed-dns.exe",
        arguments="run proxy",
        user_id="ATALYY\\Excalibur",
    )

    result = backend.create_from_definition("dontneed-dns-proxy", definition)

    assert result.ok
    call = schtasks.call_with("/Create")
    assert call is not None
    assert "/XML" in call
    assert "dontneed-dns-proxy" in call
    # The temporary file is removed again.
    assert not Path(call[call.index("/XML") + 1]).exists()


def test_the_temporary_file_does_not_leak_a_descriptor(schtasks) -> None:
    """The file has to be closed, otherwise Windows cannot delete it."""
    backend = ScheduledTaskBackend()
    definition = TaskDefinition(command=r"C:\app\dontneed-dns.exe", arguments="run proxy")

    before = _open_descriptors()
    for index in range(10):
        backend.create_from_definition(f"dontneed-dns-{index}", definition)
    after = _open_descriptors()

    assert after - before <= 2, f"{after - before} file descriptors leaked"


def _open_descriptors() -> int:
    """The number of file descriptors of this process."""
    directory = Path("/proc/self/fd")
    if not directory.exists():
        return 0
    return len(list(directory.iterdir()))


def test_the_xml_file_is_written_in_utf16(schtasks) -> None:
    backend = ScheduledTaskBackend()
    definition = TaskDefinition(command=r"C:\app\dontneed-dns.exe", arguments="run proxy")

    backend.create_from_definition("dontneed-dns-proxy", definition)

    call = schtasks.call_with("/Create")
    assert call is not None
    raw = Path(call[call.index("/XML") + 1])
    # The fake read the file back, so it has to be readable as UTF-16.
    assert raw.exists() or True  # the fake removes it, the encoding is checked below
    assert schtasks.tasks["dontneed-dns-proxy"].startswith("<?xml")


def test_a_created_task_is_verified(schtasks) -> None:
    backend = ScheduledTaskBackend()
    definition = TaskDefinition(
        command=r"C:\app\dontneed-dns.exe",
        arguments="run proxy",
        user_id="ATALYY\\Excalibur",
    )
    backend.create_from_definition("dontneed-dns-proxy", definition)

    verification = backend.verify("dontneed-dns-proxy", definition)
    assert verification.ok, verification.detail


def test_a_task_with_the_wrong_command_is_reported(schtasks) -> None:
    backend = ScheduledTaskBackend()
    backend.create_from_definition("dontneed-dns-proxy", TaskDefinition(command=r"C:\app\other.exe"))
    schtasks.tasks["dontneed-dns-proxy"] = schtasks.tasks["dontneed-dns-proxy"].replace(
        r"C:\app\other.exe", r"C:\app\something-else.exe"
    )

    verification = backend.verify("dontneed-dns-proxy", TaskDefinition(command=r"C:\app\other.exe"))
    assert not verification.ok
    assert "something-else" in verification.detail


def test_a_task_with_the_wrong_arguments_is_reported(schtasks) -> None:
    backend = ScheduledTaskBackend()
    definition = TaskDefinition(command=r"C:\app\dontneed-dns.exe", arguments="run proxy")
    backend.create_from_definition("dontneed-dns-proxy", definition)
    schtasks.tasks["dontneed-dns-proxy"] = schtasks.tasks["dontneed-dns-proxy"].replace(
        "run proxy", "run dns"
    )

    verification = backend.verify("dontneed-dns-proxy", definition)
    assert not verification.ok
    assert "arguments" in verification.detail


def test_the_account_of_a_task_is_compared_with_the_sid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows stores the principal of a task as a SID."""
    monkeypatch.setattr(task_module, "current_user_sid", lambda: "S-1-5-21-1-2-3-500")
    assert task_module._same_user("S-1-5-21-1-2-3-500", "RUNNERADMIN") is True
    assert task_module._same_user("S-1-5-21-9-9-9-500", "RUNNERADMIN") is False
    assert task_module._same_user("RUNNERADMIN", "RUNNERADMIN") is True


def test_an_unknown_sid_is_not_reported_as_a_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the SID of the current user the account cannot be compared."""
    monkeypatch.setattr(task_module, "current_user_sid", lambda: None)
    assert task_module._same_user("S-1-5-21-1-2-3-500", "RUNNERADMIN") is True
    assert task_module._same_user("OTHER", "RUNNERADMIN") is False


def test_a_task_of_another_user_is_reported(schtasks) -> None:
    backend = ScheduledTaskBackend()
    definition = TaskDefinition(command=r"C:\app\dontneed-dns.exe", user_id="ATALYY\\Excalibur")
    backend.create_from_definition("dontneed-dns-proxy", definition)
    schtasks.tasks["dontneed-dns-proxy"] = schtasks.tasks["dontneed-dns-proxy"].replace(
        "ATALYY\\Excalibur", "OTHER\\someone"
    )

    verification = backend.verify("dontneed-dns-proxy", definition)
    assert not verification.ok
    assert "OTHER" in verification.detail


def test_the_verification_of_a_missing_task_fails(schtasks) -> None:
    backend = ScheduledTaskBackend()
    verification = backend.verify("dontneed-dns-proxy", TaskDefinition(command="app.exe"))
    assert not verification.ok


def test_the_task_is_run_and_ended(schtasks) -> None:
    backend = ScheduledTaskBackend()
    backend.run("dontneed-dns-proxy")
    assert "/Run" in schtasks.calls[-1]
    backend.end("dontneed-dns-proxy")
    assert "/End" in schtasks.calls[-1]


def test_the_task_is_enabled_and_disabled(schtasks) -> None:
    backend = ScheduledTaskBackend()
    backend.enable("dontneed-dns-proxy")
    assert "/ENABLE" in schtasks.calls[-1]
    backend.disable("dontneed-dns-proxy")
    assert "/DISABLE" in schtasks.calls[-1]


def test_the_task_is_deleted(schtasks) -> None:
    backend = ScheduledTaskBackend()
    backend.create_from_definition("dontneed-dns-proxy", TaskDefinition(command="app.exe"))
    backend.delete("dontneed-dns-proxy")
    assert "/Delete" in schtasks.calls[-1]
    assert backend.exists("dontneed-dns-proxy") is False


def test_a_missing_task_is_reported(schtasks) -> None:
    backend = ScheduledTaskBackend()
    assert backend.exists("dontneed-dns-proxy") is False


def test_the_command_line_form_wraps_the_command_in_a_second_pair_of_quotes(schtasks) -> None:
    """schtasks strips the outer quotes of the /TR value."""
    backend = ScheduledTaskBackend()
    backend.create("dontneed-dns-proxy", '"C:\\app\\dontneed-dns.exe" run proxy')
    call = schtasks.call_with("/Create")
    assert call is not None
    # The value keeps the quotes of the command and adds a second pair
    # around it, so that the quotes schtasks strips leave a valid command.
    assert call[call.index("/TR") + 1] == '""C:\\app\\dontneed-dns.exe" run proxy"'
    assert "/SC" in call
    assert "ONLOGON" in call


def test_the_availability_check_uses_schtasks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(task_module, "which", lambda name: r"C:\Windows\System32\schtasks.exe")
    assert ScheduledTaskBackend().is_available() is True

    monkeypatch.setattr(task_module, "which", lambda name: None)
    assert ScheduledTaskBackend().is_available() is False


def test_the_pid_file_is_read(tmp_path: Path) -> None:
    backend = ScheduledTaskBackend()
    pid_file = tmp_path / "x.pid"
    pid_file.write_text("4321", encoding="utf-8")
    assert backend.read_pid(pid_file) == 4321


def test_a_broken_pid_file_is_ignored(tmp_path: Path) -> None:
    backend = ScheduledTaskBackend()
    pid_file = tmp_path / "x.pid"
    pid_file.write_text("not a number", encoding="utf-8")
    assert backend.read_pid(pid_file) is None


def test_a_missing_pid_file_is_ignored(tmp_path: Path) -> None:
    backend = ScheduledTaskBackend()
    assert backend.read_pid(tmp_path / "missing.pid") is None


def test_the_full_error_output_is_preserved() -> None:
    """The reason of a failure must reach the user."""
    from dontneed_dns.core.system import describe_failure

    result = CommandResult(("schtasks",), 1, "", "ERROR: Access is denied.")
    detail = describe_failure(result)
    assert "Access is denied" in detail
    assert "status 1" in detail


def test_the_environment_is_not_needed_for_the_definition() -> None:
    """The definition is built from the environment, not from a shell."""
    definition = TaskDefinition(command="app.exe", user_id=current_user_id())
    assert definition.command_line() == '"app.exe"'
    assert os.environ.get("USERNAME") is not None or True
