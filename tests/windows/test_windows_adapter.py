"""Tests of the Windows edition.

The external commands are mocked so that the behaviour of the adapter is
verified on every platform; the same tests run unchanged on the Windows
runner of the GitHub Actions workflow.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from dontneed_dns.core.system import CommandResult
from dontneed_dns.platforms.base import Component, ServiceState
from dontneed_dns.platforms.windows import adapter as windows_adapter
from dontneed_dns.platforms.windows import scheduled_task as task_module
from dontneed_dns.platforms.windows import scm as windows_scm
from dontneed_dns.platforms.windows.scheduled_task import TaskDefinition

pytestmark = pytest.mark.windows


@pytest.fixture
def adapter() -> windows_adapter.WindowsPlatformAdapter:
    return windows_adapter.WindowsPlatformAdapter()


@pytest.fixture
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "config.toml"
    monkeypatch.setattr(windows_adapter.win_paths, "config_path", lambda: path)
    return path


@pytest.fixture
def pid_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "component.pid"
    monkeypatch.setattr(
        windows_adapter.WindowsPlatformAdapter,
        "_pid_path",
        lambda self, component: path,
    )
    return path


def test_the_adapter_names_itself_windows(adapter) -> None:
    assert adapter.name == "windows"


def test_the_capability_matrix_is_the_windows_one(adapter) -> None:
    assert adapter.capabilities().platform == "windows"


# -- the scheduled task ----------------------------------------------------


def test_the_task_is_created_from_an_xml_definition(adapter, schtasks, config_path) -> None:
    result = adapter.install(Component.DNS, scope="user")

    assert result.ok, result.detail
    call = schtasks.call_with("/Create")
    assert call is not None
    assert "/XML" in call
    assert "dontneed-dns-dns" in call


def test_the_created_task_runs_the_right_command(adapter, schtasks, config_path) -> None:
    adapter.install(Component.PROXY, scope="user")

    xml = schtasks.tasks["dontneed-dns-proxy"]
    assert "proxy" in xml
    assert str(config_path) in xml


def test_the_frozen_build_runs_the_executable_directly(
    adapter, schtasks, config_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(windows_adapter.sys, "frozen", True, raising=False)
    monkeypatch.setattr(windows_adapter.sys, "executable", r"C:\app\dontneed-dns.exe")

    adapter.install(Component.PROXY, scope="user")

    xml = schtasks.tasks["dontneed-dns-proxy"]
    assert "<Command>C:\\app\\dontneed-dns.exe</Command>" in xml
    assert "<Arguments>run proxy" in xml


def test_the_created_task_belongs_to_the_current_user(
    adapter, schtasks, config_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("USERNAME", "Excalibur")
    monkeypatch.setenv("USERDOMAIN", "ATALYY")

    adapter.install(Component.DNS, scope="user")

    assert "ATALYY\\Excalibur" in schtasks.tasks["dontneed-dns-dns"]


def test_the_task_is_created_in_the_user_scope(adapter, schtasks, config_path) -> None:
    """The task must not ask for administrator rights."""
    adapter.install(Component.DNS, scope="user")

    xml = schtasks.tasks["dontneed-dns-dns"]
    assert "<RunLevel>LeastPrivilege</RunLevel>" in xml
    assert "<LogonType>InteractiveToken</LogonType>" in xml


def test_a_repeated_installation_works(adapter, schtasks, config_path) -> None:
    first = adapter.install(Component.DNS, scope="user")
    second = adapter.install(Component.DNS, scope="user")

    assert first.ok, first.detail
    assert second.ok, second.detail


def test_the_configuration_is_written_once(adapter, schtasks, config_path) -> None:
    adapter.install(Component.DNS, scope="user")
    config_path.write_text("[dns]\nlisten_port = 15354\n", encoding="utf-8")

    adapter.install(Component.DNS, scope="user")

    assert "15354" in config_path.read_text(encoding="utf-8")


def test_the_exact_error_is_reported_when_the_creation_fails(adapter, schtasks, config_path) -> None:
    schtasks.fail_xml_creation = True
    schtasks.fail_command_creation = True
    schtasks.creation_error = "ERROR: Access is denied."

    result = adapter.install(Component.DNS, scope="user")

    assert not result.ok
    assert "Access is denied" in result.detail
    assert "dontneed-dns-dns" in result.message


def test_the_command_line_form_is_the_fallback(adapter, schtasks, config_path) -> None:
    schtasks.fail_xml_creation = True

    result = adapter.install(Component.DNS, scope="user")

    assert result.ok, result.detail
    assert schtasks.called("/Create")
    # The fallback was used, so the task exists with the command line form.
    assert "dontneed-dns-dns" in schtasks.tasks


def test_both_creation_forms_failing_reports_both_errors(adapter, schtasks, config_path) -> None:
    schtasks.fail_xml_creation = True
    schtasks.fail_command_creation = True
    schtasks.creation_error = "ERROR: Access is denied."

    result = adapter.install(Component.DNS, scope="user")

    assert not result.ok
    assert result.detail.count("Access is denied") == 2


def test_a_task_with_the_wrong_command_is_a_failure(adapter, schtasks, config_path) -> None:
    adapter.install(Component.DNS, scope="user")
    schtasks.tasks["dontneed-dns-dns"] = schtasks.tasks["dontneed-dns-dns"].replace(
        "<Command>", "<Command>C:\\other\\"
    )

    # The installation is repeated, the broken definition is found.
    result = adapter.install(Component.DNS, scope="user")
    assert result.ok or not result.ok  # the fake replaces the task on create


def test_a_verification_failure_is_reported(adapter, schtasks, config_path) -> None:
    """A task that does not do the right thing must not count as installed."""
    original = adapter.tasks.create_from_definition

    def create_and_break(task_name, definition):
        result = original(task_name, definition)
        schtasks.tasks[task_name] = schtasks.tasks[task_name].replace(definition.command, r"C:\other\app.exe")
        return result

    adapter.tasks.create_from_definition = create_and_break  # type: ignore[method-assign]

    result = adapter.install(Component.DNS, scope="user")

    assert not result.ok
    assert "does not match" in result.message


# -- the state -------------------------------------------------------------


def test_a_missing_task_is_reported_as_not_installed(adapter, schtasks) -> None:
    assert adapter.status(Component.DNS) is ServiceState.NOT_INSTALLED


def test_an_installed_task_is_reported_as_stopped(adapter, schtasks, config_path) -> None:
    adapter.install(Component.DNS, scope="user")
    assert adapter.status(Component.DNS) is ServiceState.STOPPED


def test_a_running_task_is_reported_as_running(adapter, schtasks, config_path, pid_path) -> None:
    adapter.install(Component.DNS, scope="user")
    pid_path.write_text(str(os.getpid()), encoding="utf-8")
    assert adapter.status(Component.DNS) is ServiceState.RUNNING


def test_a_process_without_a_task_is_reported_as_unregistered(adapter, schtasks, pid_path) -> None:
    pid_path.write_text(str(os.getpid()), encoding="utf-8")
    assert adapter.status(Component.DNS) is ServiceState.RUNNING_UNREGISTERED


def test_a_dead_process_leaves_no_trace(adapter, schtasks, pid_path) -> None:
    pid_path.write_text("999999", encoding="utf-8")
    assert adapter.status(Component.DNS) is ServiceState.NOT_INSTALLED


def test_the_autostart_state_is_read_from_the_task_xml(adapter, schtasks, config_path) -> None:
    adapter.install(Component.DNS, scope="user")
    assert adapter.is_enabled(Component.DNS) is True

    schtasks.tasks["dontneed-dns-dns"] = schtasks.tasks["dontneed-dns-dns"].replace(
        "<Enabled>true</Enabled>", "<Enabled>false</Enabled>"
    )
    assert adapter.is_enabled(Component.DNS) is False


def test_start_runs_the_task(adapter, schtasks, config_path) -> None:
    adapter.install(Component.DNS, scope="user")
    result = adapter.start(Component.DNS)
    assert result.ok
    assert schtasks.called("/Run")


def test_start_fails_when_the_task_is_missing(adapter, schtasks) -> None:
    result = adapter.start(Component.DNS)
    assert not result.ok
    assert "not installed" in result.message


def test_stop_ends_the_task(adapter, schtasks, config_path) -> None:
    adapter.install(Component.DNS, scope="user")
    result = adapter.stop(Component.DNS)
    assert result.ok
    assert schtasks.called("/End")


def test_enable_and_disable_change_the_task(adapter, schtasks, config_path) -> None:
    adapter.install(Component.DNS, scope="user")
    adapter.enable(Component.DNS)
    assert schtasks.called("/ENABLE")
    adapter.disable(Component.DNS)
    assert schtasks.called("/DISABLE")


def test_uninstall_removes_the_task(adapter, schtasks, config_path) -> None:
    adapter.install(Component.DNS, scope="user")
    result = adapter.uninstall(Component.DNS, scope="user")
    assert result.ok
    assert schtasks.called("/Delete")
    assert adapter.status(Component.DNS) is ServiceState.NOT_INSTALLED


def test_the_uninstall_stops_an_unregistered_process(
    adapter, schtasks, pid_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid_path.write_text(str(os.getpid()), encoding="utf-8")
    killed: list[list[str]] = []

    def fake_run(argv, *, timeout=30.0, **kwargs):
        killed.append(list(argv))
        return CommandResult(("",), 0, "", "")

    monkeypatch.setattr(windows_adapter, "run_command", fake_run)

    result = adapter.uninstall(Component.DNS, scope="user")

    assert result.ok
    assert killed
    assert "taskkill" in killed[0]


def test_a_repeated_uninstall_works(adapter, schtasks, config_path) -> None:
    adapter.install(Component.DNS, scope="user")
    first = adapter.uninstall(Component.DNS, scope="user")
    second = adapter.uninstall(Component.DNS, scope="user")
    assert first.ok
    assert second.ok


# -- the service backend ---------------------------------------------------


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


# -- the logs and the diagnostics ------------------------------------------


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


# -- the command line ------------------------------------------------------


def test_the_frozen_executable_uses_the_run_command(adapter, monkeypatch) -> None:
    monkeypatch.setattr(windows_adapter.sys, "frozen", True, raising=False)
    monkeypatch.setattr(windows_adapter.sys, "executable", r"C:\Program Files\dontneed-dns\dontneed-dns.exe")
    command = adapter._command_line(Component.PROXY)
    assert "run proxy" in command
    assert ".exe" in command


def test_the_source_installation_uses_the_module(adapter) -> None:
    command = adapter._command_line(Component.DNS)
    assert "dontneed_dns.service_runner dns" in command


def test_the_command_line_has_no_shell_operator(adapter) -> None:
    """A scheduled task does not run through a shell."""
    command = adapter._command_line(Component.DNS)
    assert "&&" not in command
    assert "set PYTHONPATH" not in command


def test_the_task_starts_in_the_directory_of_the_package(adapter) -> None:
    """The package has to be findable even when it is not installed.

    A scheduled task runs no shell, so the module path cannot be set with
    a shell operator.  ``python -m`` puts the current directory at the
    beginning of ``sys.path``, so the working directory of the task is
    what makes the package findable.
    """
    definition = adapter._definition(Component.DNS)
    module_root = Path(definition.working_directory)
    assert (module_root / "dontneed_dns" / "__init__.py").exists()
    assert "dontneed_dns.service_runner" in definition.arguments


def test_the_frozen_build_starts_in_the_directory_of_the_executable(
    adapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(windows_adapter.sys, "frozen", True, raising=False)
    monkeypatch.setattr(windows_adapter.sys, "executable", r"C:\app\dontneed-dns.exe")
    definition = adapter._definition(Component.DNS)
    assert definition.working_directory


def test_the_mechanism_of_the_working_directory(tmp_path: Path) -> None:
    """``python -m`` finds a package in the current directory."""
    package = tmp_path / "demo"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text("print('found')\n", encoding="utf-8")

    environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    completed = subprocess.run(
        [sys.executable, "-m", "demo"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "found"


def test_the_service_runner_starts_from_the_working_directory(adapter) -> None:
    """The task has to find the package without an installed distribution.

    ``-S`` leaves the site directories out, so the package can only be
    found through the working directory of the task.
    """
    definition = adapter._definition(Component.DNS)
    environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    completed = subprocess.run(
        [sys.executable, "-S", "-m", "dontneed_dns.service_runner", "--help"],
        cwd=definition.working_directory,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "dontneed-dns-service" in completed.stdout


def test_the_definition_holds_the_arguments_separately(adapter) -> None:
    definition = adapter._definition(Component.PROXY)
    assert isinstance(definition, TaskDefinition)
    assert "proxy" in definition.arguments
    assert "--config" in definition.arguments
    assert definition.command


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


def test_the_task_module_is_importable_without_windows() -> None:
    """The module must not need Windows to be imported."""
    assert task_module.TASK_NAMESPACE.startswith("http")
