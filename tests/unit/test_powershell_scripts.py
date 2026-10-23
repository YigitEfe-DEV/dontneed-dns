"""Tests of the PowerShell installation scripts.

The scripts are parsed and executed with PowerShell when it is available.
The Windows runner of the workflow always has it, and a developer can
install it on Linux with ``pwsh``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PACKAGING = PROJECT_ROOT / "packaging"
PWSH = shutil.which("pwsh") or shutil.which("powershell")

pytestmark = pytest.mark.skipif(PWSH is None, reason="PowerShell is not available")


def _run_script(script: Path, arguments: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PWSH or "pwsh", "-NoProfile", "-File", str(script), *arguments],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _stub_executable(
    directory: Path,
    *,
    exit_code: int = 0,
    message: str = "",
    fail_on: str = "install",
) -> Path:
    """A stand in for the portable executable.

    On Linux a file with a shebang is executed by PowerShell like a real
    program, so the behaviour of the installer can be checked without
    Windows.  The stub fails only for the given command, so that the
    script reaches the step that is being tested.
    """
    directory.mkdir(parents=True, exist_ok=True)
    stub = directory / "dontneed-dns.exe"
    lines = ["#!/bin/sh", 'echo "dontneed-dns 0.1.2"']
    if message:
        lines += [
            'case "$*" in',
            f'  *{fail_on}*) echo "ERROR: {message}" >&2; exit {exit_code} ;;',
            "esac",
        ]
    lines.append("exit 0")
    stub.write_text("\n".join(lines) + "\n", encoding="utf-8")
    stub.chmod(0o755)
    return stub


def _package(tmp_path: Path, *, exit_code: int = 0, message: str = "") -> Path:
    """A directory that looks like the unpacked Windows archive."""
    package = tmp_path / "package"
    package.mkdir(parents=True, exist_ok=True)
    _stub_executable(package, exit_code=exit_code, message=message)
    for name in ("install.ps1", "uninstall.ps1"):
        shutil.copy2(PACKAGING / "windows" / name, package / name)
    return package


@pytest.fixture
def windows_env(tmp_path: Path) -> dict[str, str]:
    """An environment that looks like a Windows user profile."""
    env = dict(os.environ)
    env["LOCALAPPDATA"] = str(tmp_path / "Local")
    env["APPDATA"] = str(tmp_path / "Roaming")
    env["USERPROFILE"] = str(tmp_path)
    return env


def test_the_installation_scripts_parse() -> None:
    completed = subprocess.run(
        [
            PWSH or "pwsh",
            "-NoProfile",
            "-File",
            str(PROJECT_ROOT / "scripts" / "check_powershell.ps1"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "syntax ok" in completed.stdout


def test_the_installation_fails_with_the_exact_error(tmp_path: Path, windows_env) -> None:
    """The reason of a failure has to reach the user."""
    package = _package(tmp_path, exit_code=1, message="Access is denied.")

    completed = _run_script(package / "install.ps1", [], windows_env)

    output = completed.stdout + completed.stderr
    assert completed.returncode != 0
    # The exact reason of the failure has to be visible.
    assert "Access is denied" in output
    # And the user has to be told what can be done about it.
    assert "elevated" in output


def test_the_installation_writes_a_log_file(tmp_path: Path, windows_env) -> None:
    """The output of the installation has to be kept for a bug report."""
    package = _package(tmp_path, exit_code=1, message="Access is denied.")

    completed = _run_script(package / "install.ps1", [], windows_env)

    assert completed.returncode != 0
    log_file = Path(windows_env["LOCALAPPDATA"]) / "dontneed-dns" / "install-output.txt"
    assert log_file.exists(), completed.stdout + completed.stderr
    content = log_file.read_text(encoding="utf-8")
    assert "Access is denied" in content


def test_a_successful_installation_is_reported(tmp_path: Path, windows_env) -> None:
    package = _package(tmp_path)

    completed = _run_script(package / "install.ps1", [], windows_env)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "dontneed-dns is installed" in completed.stdout
    installed = Path(windows_env["LOCALAPPDATA"]) / "dontneed-dns" / "dontneed-dns.exe"
    assert installed.exists()


def test_the_uninstall_removes_the_installation(tmp_path: Path, windows_env) -> None:
    package = _package(tmp_path)

    _run_script(package / "install.ps1", [], windows_env)
    completed = _run_script(package / "uninstall.ps1", [], windows_env)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "was removed" in completed.stdout
    assert not (Path(windows_env["LOCALAPPDATA"]) / "dontneed-dns").exists()
