"""Tests of the command line interface."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from dontneed_dns.cli import main as cli
from dontneed_dns.core.capability import LINUX_MATRIX
from dontneed_dns.core.config import config_to_toml, default_config
from dontneed_dns.platforms.base import Component, OperationResult, ServiceState

runner = CliRunner()


class FakeAdapter:
    """A platform adapter that records what was asked of it."""

    name = "linux"
    scope = "user"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.state = ServiceState.RUNNING
        self.enabled = True
        #: The components whose installation fails.
        self.fail_install: set[str] = set()

    def is_available(self) -> bool:
        return True

    def describe(self) -> str:
        return "fake platform"

    def capabilities(self):
        return LINUX_MATRIX

    def diagnostics(self) -> dict:
        return {"platform": "fake"}

    def status(self, component: Component) -> ServiceState:
        return self.state

    def is_enabled(self, component: Component) -> bool | None:
        return self.enabled

    def start(self, component: Component) -> OperationResult:
        self.calls.append(("start", component.value))
        return OperationResult.success("started")

    def stop(self, component: Component) -> OperationResult:
        self.calls.append(("stop", component.value))
        return OperationResult.success("stopped")

    def restart(self, component: Component) -> OperationResult:
        self.calls.append(("restart", component.value))
        return OperationResult.success("restarted")

    def enable(self, component: Component) -> OperationResult:
        self.calls.append(("enable", component.value))
        return OperationResult.success("enabled")

    def disable(self, component: Component) -> OperationResult:
        self.calls.append(("disable", component.value))
        return OperationResult.success("disabled")

    def logs(self, component: Component, lines: int = 50) -> OperationResult:
        return OperationResult.success("logs", data={"output": f"{component.value} log line"})

    def install(self, component: Component, *, scope: str = "user") -> OperationResult:
        self.calls.append(("install", f"{component.value}:{scope}"))
        if component.value in self.fail_install:
            return OperationResult.failure(
                f"the task dontneed-dns-{component.value} could not be created.",
                detail="schtasks exited with status 1\nERROR: Access is denied.",
            )
        return OperationResult.success("installed")

    def uninstall(self, component: Component, *, scope: str = "user") -> OperationResult:
        self.calls.append(("uninstall", f"{component.value}:{scope}"))
        return OperationResult.success("removed")

    def install_paths(self, scope: str = "user") -> dict[str, Path]:
        return {"config": Path("/tmp/config.toml")}


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> FakeAdapter:
    fake = FakeAdapter()
    monkeypatch.setattr(cli, "create_adapter", lambda: fake)
    return fake


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(config_to_toml(default_config()), encoding="utf-8")
    return path


def test_version(adapter: FakeAdapter) -> None:
    result = runner.invoke(cli.app, ["version"])
    assert result.exit_code == 0
    assert "dontneed-dns" in result.stdout


def test_the_version_option_prints_the_version(adapter: FakeAdapter) -> None:
    result = runner.invoke(cli.app, ["--version"])
    assert result.exit_code == 0
    assert "dontneed-dns" in result.stdout


def test_without_a_command_the_help_is_printed(adapter: FakeAdapter) -> None:
    result = runner.invoke(cli.app, [])
    assert "Usage" in result.stdout


def test_status(adapter: FakeAdapter, config_path: Path) -> None:
    result = runner.invoke(cli.app, ["status", "--config", str(config_path)])
    assert result.exit_code == 0
    assert "DNS resolver" in result.stdout
    assert "Running" in result.stdout


def test_dns_start(adapter: FakeAdapter, config_path: Path) -> None:
    result = runner.invoke(cli.app, ["dns", "start", "--config", str(config_path)])
    assert result.exit_code == 0
    assert ("start", "dns") in adapter.calls


def test_proxy_stop(adapter: FakeAdapter, config_path: Path) -> None:
    result = runner.invoke(cli.app, ["proxy", "stop", "--config", str(config_path)])
    assert result.exit_code == 0
    assert ("stop", "proxy") in adapter.calls


def test_restart(adapter: FakeAdapter, config_path: Path) -> None:
    result = runner.invoke(cli.app, ["dns", "restart", "--config", str(config_path)])
    assert result.exit_code == 0
    assert ("restart", "dns") in adapter.calls


def test_enable_and_disable(adapter: FakeAdapter, config_path: Path) -> None:
    runner.invoke(cli.app, ["dns", "enable", "--config", str(config_path)])
    runner.invoke(cli.app, ["dns", "disable", "--config", str(config_path)])
    assert ("enable", "dns") in adapter.calls
    assert ("disable", "dns") in adapter.calls


def test_logs(adapter: FakeAdapter, config_path: Path) -> None:
    result = runner.invoke(cli.app, ["logs", "--config", str(config_path)])
    assert result.exit_code == 0
    assert "dns log line" in result.stdout


def test_config_validate_accepts_a_valid_file(config_path: Path) -> None:
    result = runner.invoke(cli.app, ["config", "validate", "--config", str(config_path)])
    assert result.exit_code == 0
    assert "valid" in result.stdout


def test_config_validate_rejects_a_broken_file(tmp_path: Path) -> None:
    broken = tmp_path / "broken.toml"
    broken.write_text("[dns]\nlisten_port = 99999\n", encoding="utf-8")
    result = runner.invoke(cli.app, ["config", "validate", "--config", str(broken)])
    assert result.exit_code == 2


def test_config_show_prints_the_configuration(config_path: Path) -> None:
    result = runner.invoke(cli.app, ["config", "show", "--config", str(config_path)])
    assert result.exit_code == 0
    assert "listen_port = 5354" in result.stdout


def test_config_init_creates_a_file(tmp_path: Path) -> None:
    target = tmp_path / "new.toml"
    result = runner.invoke(cli.app, ["config", "init", "--config", str(target)])
    assert result.exit_code == 0
    assert target.exists()


def test_config_init_refuses_to_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "new.toml"
    target.write_text("x = 1\n", encoding="utf-8")
    result = runner.invoke(cli.app, ["config", "init", "--config", str(target)])
    assert result.exit_code == 1


def test_install_installs_both_components(adapter: FakeAdapter, config_path: Path) -> None:
    result = runner.invoke(cli.app, ["install", "--config", str(config_path), "--yes", "--scope", "user"])
    assert result.exit_code == 0
    assert ("install", "dns:user") in adapter.calls
    assert ("install", "proxy:user") in adapter.calls


def test_a_partial_installation_is_reported(adapter: FakeAdapter, config_path: Path) -> None:
    adapter.fail_install = {"proxy"}
    result = runner.invoke(cli.app, ["install", "--config", str(config_path), "--yes", "--scope", "user"])
    assert result.exit_code == 1
    assert "incomplete" in result.output.lower()
    assert "Access is denied" in result.output


def test_a_failed_installation_keeps_the_configuration(adapter: FakeAdapter, config_path: Path) -> None:
    """A failure must not delete the configuration of the user."""
    adapter.fail_install = {"dns", "proxy"}
    runner.invoke(cli.app, ["install", "--config", str(config_path), "--yes", "--scope", "user"])
    assert config_path.exists()


def test_the_status_reports_an_unregistered_process(adapter: FakeAdapter, config_path: Path) -> None:
    adapter.state = ServiceState.RUNNING_UNREGISTERED
    result = runner.invoke(cli.app, ["status", "--config", str(config_path)])
    assert result.exit_code == 0
    assert "not registered" in result.output


def test_uninstall_removes_both_components(adapter: FakeAdapter, config_path: Path) -> None:
    result = runner.invoke(cli.app, ["uninstall", "--config", str(config_path), "--yes", "--scope", "user"])
    assert result.exit_code == 0
    assert ("uninstall", "dns:user") in adapter.calls
    assert ("uninstall", "proxy:user") in adapter.calls


def test_doctor_produces_a_report(adapter: FakeAdapter, config_path: Path) -> None:
    result = runner.invoke(cli.app, ["doctor", "--config", str(config_path)])
    assert result.exit_code == 0
    assert '"platform"' in result.stdout


def test_check_runs_the_health_checks(adapter: FakeAdapter, config_path: Path) -> None:
    result = runner.invoke(cli.app, ["check", "--config", str(config_path)])
    assert result.exit_code in (0, 1)
    assert "Health checks" in result.stdout


def test_launch_reports_a_missing_command(adapter: FakeAdapter, config_path: Path) -> None:
    result = runner.invoke(cli.app, ["launch", "--config", str(config_path), "definitely-not-a-command-xyz"])
    assert result.exit_code == 1


def test_an_unsupported_platform_is_reported(monkeypatch: pytest.MonkeyPatch, config_path: Path) -> None:
    from dontneed_dns.core.errors import UnsupportedPlatformError

    def raise_unsupported() -> FakeAdapter:
        raise UnsupportedPlatformError("nope")

    monkeypatch.setattr(cli, "create_adapter", raise_unsupported)
    result = runner.invoke(cli.app, ["status", "--config", str(config_path)])
    assert result.exit_code == 3
