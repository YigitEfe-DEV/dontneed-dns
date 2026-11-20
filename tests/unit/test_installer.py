"""Unit tests of the self contained installer."""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

import pytest

from dontneed_dns.core.config import default_config
from dontneed_dns.core.paths import bin_dir, install_dir
from dontneed_dns.installer import Installer, main
from dontneed_dns.platforms.base import Component, ServiceState
from dontneed_dns.platforms.linux.adapter import LinuxPlatformAdapter


class FakeAdapter:
    """Records the calls of the installer."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def install(self, component: Component, *, scope: str = "user"):
        self.calls.append(f"install:{component.value}")
        from dontneed_dns.platforms.base import OperationResult

        return OperationResult.success("installed")

    def uninstall(self, component: Component, *, scope: str = "user"):
        self.calls.append(f"uninstall:{component.value}")
        from dontneed_dns.platforms.base import OperationResult

        return OperationResult.success("removed")

    def enable(self, component: Component):
        self.calls.append(f"enable:{component.value}")
        from dontneed_dns.platforms.base import OperationResult

        return OperationResult.success("enabled")

    def disable(self, component: Component):
        self.calls.append(f"disable:{component.value}")
        from dontneed_dns.platforms.base import OperationResult

        return OperationResult.success("disabled")

    def start(self, component: Component):
        self.calls.append(f"start:{component.value}")
        from dontneed_dns.platforms.base import OperationResult

        return OperationResult.success("started")

    def stop(self, component: Component):
        self.calls.append(f"stop:{component.value}")
        from dontneed_dns.platforms.base import OperationResult

        return OperationResult.success("stopped")

    def status(self, component: Component) -> ServiceState:
        self.calls.append(f"status:{component.value}")
        return ServiceState.STOPPED

    def is_enabled(self, component: Component) -> bool | None:
        return True

    def is_available(self) -> bool:
        return True

    def install_transparent(self, *, user: str | None = None):
        from dontneed_dns.platforms.base import OperationResult

        self.calls.append("install_transparent")
        return OperationResult.success("the transparent router is installed")

    def uninstall_transparent(self):
        from dontneed_dns.platforms.base import OperationResult

        self.calls.append("uninstall_transparent")
        return OperationResult.success("the transparent router is removed")

    def transparent_status(self):
        from dontneed_dns.platforms.base import ServiceState

        return ServiceState.NOT_INSTALLED

    def describe(self) -> str:
        return "fake"

    def capabilities(self):
        raise NotImplementedError

    def diagnostics(self):
        return {}

    def unit_name(self, component: Component) -> str:
        return f"dontneed-dns-{component.value}.service"

    def unit_path(self, component: Component, scope: str = "user") -> Path:
        return Path("/tmp") / self.unit_name(component)


@pytest.fixture
def installer(tmp_path: Path) -> Installer:
    payload = tmp_path / "payload"
    payload.write_bytes(b"#!/bin/sh\necho payload\n")
    return Installer(
        config_path=tmp_path / "config.toml",
        payload_source=payload,
        target_binary=tmp_path / "bin" / "dontneed-dns",
        adapter=FakeAdapter(),  # type: ignore[arg-type]
        health_runner=lambda config: [],
        startup_wait=0.0,
    )


# ---------------------------------------------------------------- the CLI


def test_help_prints_the_usage(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--help"]) == 0
    output = capsys.readouterr().out
    assert "Usage:" in output
    assert "--uninstall" in output
    assert "--status" in output


def test_version_prints_the_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version"]) == 0
    assert "dontneed-dns" in capsys.readouterr().out


def test_the_installer_runs_without_arguments(installer, monkeypatch, capsys) -> None:
    monkeypatch.setattr("dontneed_dns.installer.Installer", lambda **kwargs: installer)
    assert main([]) == 0
    assert "installation is complete" in capsys.readouterr().out


# ------------------------------------------------------------ the checks


def test_the_architecture_is_verified(installer, monkeypatch) -> None:
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    step = installer.check_architecture()
    assert step.ok
    assert "x86_64" in step.detail


def test_an_unsupported_architecture_is_refused(installer, monkeypatch) -> None:
    monkeypatch.setattr(platform, "machine", lambda: "aarch64")
    step = installer.check_architecture()
    assert not step.ok
    assert step.fatal


def test_a_missing_user_session_is_refused(installer, monkeypatch) -> None:
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    step = installer.check_system()
    assert not step.ok
    assert step.fatal


def test_the_disk_space_is_checked(installer, tmp_path) -> None:
    step = installer.check_disk_space()
    assert step.ok
    assert "free" in step.detail


# -------------------------------------------------------- the configuration


def test_an_existing_configuration_is_preserved(installer, tmp_path) -> None:
    target = tmp_path / "config.toml"
    target.write_text('[dns]\nsplit_domains = ["discord.com", "roblox.com"]\n', encoding="utf-8")
    config, step = installer._prepare_config()
    assert step.ok
    assert "preserved" in step.detail
    assert "roblox.com" in config.dns.split_domains
    assert "roblox.com" in target.read_text(encoding="utf-8")


def test_a_missing_configuration_is_created(installer, tmp_path) -> None:
    config, step = installer._prepare_config()
    assert step.ok
    assert "created" in step.detail
    assert (tmp_path / "config.toml").exists()
    assert config.service.autostart is True


def test_the_automatic_start_is_enabled_when_the_configuration_asks_for_it(installer) -> None:
    config = default_config()
    config.service.autostart = True
    step = installer._apply_autostart(installer.adapter, config)  # type: ignore[arg-type]
    assert step.ok
    assert "enable:dns" in installer.adapter.calls  # type: ignore[attr-defined]
    assert "enable:proxy" in installer.adapter.calls  # type: ignore[attr-defined]


def test_the_automatic_start_is_disabled_when_the_configuration_forbids_it(installer) -> None:
    config = default_config()
    config.service.autostart = False
    step = installer._apply_autostart(installer.adapter, config)  # type: ignore[arg-type]
    assert step.ok
    assert "disable:dns" in installer.adapter.calls  # type: ignore[attr-defined]
    assert "enable:dns" not in installer.adapter.calls  # type: ignore[attr-defined]


# ------------------------------------------------------------ the adapter


def test_the_unit_starts_the_installed_executable(tmp_path) -> None:
    binary = tmp_path / "bin" / "dontneed-dns"
    adapter = LinuxPlatformAdapter(executable=str(binary))
    command, environment = adapter._exec_start()
    assert str(binary) in command
    assert environment == ""


def test_a_frozen_build_runs_the_component_with_the_run_command(tmp_path, monkeypatch) -> None:
    binary = tmp_path / "bin" / "dontneed-dns"
    adapter = LinuxPlatformAdapter(executable=str(binary))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    command, environment = adapter._exec_start()
    assert command.endswith("run")
    assert environment == ""


def test_a_source_installation_still_uses_the_module(monkeypatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    adapter = LinuxPlatformAdapter()
    command, _environment = adapter._exec_start()
    assert "dontneed_dns.service_runner" in command


def test_the_unit_file_contains_the_configured_path(tmp_path, monkeypatch) -> None:
    binary = tmp_path / "bin" / "dontneed-dns"
    adapter = LinuxPlatformAdapter(executable=str(binary))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    content = adapter._render_unit(Component.DNS, "user")
    assert f"ExecStart={binary} run dns --config" in content


# ------------------------------------------------------------- the paths


def test_the_installation_directory_follows_xdg_data_home(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert install_dir() == tmp_path / "dontneed-dns"
    assert bin_dir() == tmp_path / "dontneed-dns" / "bin"


def test_the_installation_directory_falls_back_to_the_home_directory(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert install_dir() == tmp_path / ".local" / "share" / "dontneed-dns"


def test_the_payload_is_installed_with_the_right_permissions(installer, tmp_path) -> None:
    installer.frozen = True
    step = installer._write_payload()
    assert step.ok
    target = tmp_path / "bin" / "dontneed-dns"
    assert target.exists()
    assert os.stat(target).st_mode & 0o777 == 0o755
