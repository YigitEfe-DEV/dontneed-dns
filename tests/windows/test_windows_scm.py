"""Tests of the Windows Service Control Manager backend.

The backend is optional: it needs administrator rights and ``pywin32``.
The commands it runs are mocked, so the behaviour is the same on every
platform.
"""

from __future__ import annotations

import pytest

from dontneed_dns.core.system import CommandResult
from dontneed_dns.platforms.windows import scm as windows_scm

pytestmark = pytest.mark.windows


@pytest.fixture
def scm(monkeypatch: pytest.MonkeyPatch):
    """A backend whose external commands are recorded."""
    backend = windows_scm.ServiceControlManagerBackend()
    calls: list[list[str]] = []

    def fake_run(argv, *, timeout=30.0, **kwargs):
        calls.append(list(argv))
        return CommandResult(tuple(argv), 0, "STATE : 4 RUNNING\n", "")

    monkeypatch.setattr(windows_scm, "run_command", fake_run)
    backend.calls = calls  # type: ignore[attr-defined]
    return backend


def test_the_service_name_of_a_component_is_remembered(scm) -> None:
    scm.register("dns", "dontneed-dns-dns")
    assert scm.service_name("dns") == "dontneed-dns-dns"


def test_an_unknown_component_gets_a_default_name(scm) -> None:
    assert scm.service_name("proxy") == "dontneed-dns-proxy"


def test_a_running_service_is_reported(scm) -> None:
    info = scm.info("dontneed-dns-dns")
    assert info.exists is True
    assert info.running is True


def test_a_service_that_does_not_exist_is_reported(scm, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(argv, *, timeout=30.0, **kwargs):
        return CommandResult(tuple(argv), 1, "", "FAILED 1060: The specified service does not exist")

    monkeypatch.setattr(windows_scm, "run_command", fake_run)
    info = scm.info("dontneed-dns-dns")
    assert info.exists is False
    assert info.running is False


def test_the_start_type_is_read(scm, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(argv, *, timeout=30.0, **kwargs):
        if "qc" in argv:
            return CommandResult(tuple(argv), 0, "START_TYPE         : 2   AUTO_START\n", "")
        return CommandResult(tuple(argv), 0, "STATE : 4 RUNNING\n", "")

    monkeypatch.setattr(windows_scm, "run_command", fake_run)
    assert scm.info("dontneed-dns-dns").start_type == "2   AUTO_START"


def test_the_path_of_the_program_is_quoted_as_a_whole(scm) -> None:
    """``sc.exe`` does not use the rules of the command line.

    The whole value has to be one quoted argument and the quotes inside it
    have to be escaped.  Without that a path with a space in it is cut off
    and the service cannot be started.
    """
    scm.create(
        "dontneed-dns-dns", r'"C:\Program Files\dontneed-dns\dontneed-dns.exe" run dns', display_name="d"
    )

    call = next(c for c in scm.calls if "create" in c)
    binary = next(part for part in call if part.startswith("binPath="))
    assert binary.startswith('binPath= "')
    assert binary.endswith('"')
    # The inner quotes are escaped, so the value stays one argument.
    assert '\\"C:\\Program Files\\dontneed-dns\\dontneed-dns.exe\\"' in binary


def test_the_commands_of_the_backend_are_the_expected_ones(scm) -> None:
    scm.start("dontneed-dns-dns")
    scm.stop("dontneed-dns-dns")
    scm.delete("dontneed-dns-dns")
    scm.set_start_type("dontneed-dns-dns", "demand")

    joined = [" ".join(call) for call in scm.calls]
    assert "sc start dontneed-dns-dns" in joined
    assert "sc stop dontneed-dns-dns" in joined
    assert "sc delete dontneed-dns-dns" in joined
    assert "sc config dontneed-dns-dns start= demand" in joined


def test_the_backend_is_unavailable_without_pywin32(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(windows_scm, "which", lambda name: r"C:\Windows\System32\sc.exe")
    monkeypatch.setattr(windows_scm, "pywin32_available", lambda: False)
    assert windows_scm.ServiceControlManagerBackend().is_available() is False

    monkeypatch.setattr(windows_scm, "pywin32_available", lambda: True)
    assert windows_scm.ServiceControlManagerBackend().is_available() is True
