"""Tests of the safe subprocess helpers."""

from __future__ import annotations

import sys

from dontneed_dns.core.system import CommandResult, process_alive, run_command, which


def test_a_successful_command_is_reported() -> None:
    result = run_command([sys.executable, "-c", "print('hello')"])
    assert result.ok
    assert "hello" in result.stdout
    assert result.returncode == 0


def test_a_failing_command_is_reported() -> None:
    result = run_command([sys.executable, "-c", "import sys; sys.exit(3)"])
    assert not result.ok
    assert result.returncode == 3


def test_a_missing_command_is_reported() -> None:
    result = run_command(["definitely-not-a-command-xyz"])
    assert result.returncode == 127
    assert "command not found" in result.stderr


def test_a_timeout_is_reported() -> None:
    result = run_command([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.5)
    assert result.timed_out
    assert not result.ok


def test_arguments_are_not_interpreted_by_a_shell() -> None:
    result = run_command([sys.executable, "-c", "import sys; print(sys.argv[1])", "; rm -rf /"])
    assert result.ok
    assert result.stdout.strip() == "; rm -rf /"


def test_an_empty_argv_is_rejected() -> None:
    try:
        run_command([])
    except ValueError:
        return
    raise AssertionError("an empty command list must be rejected")


def test_which_finds_the_interpreter() -> None:
    assert which("python3") is not None or which("python") is not None


def test_which_returns_none_for_an_unknown_program() -> None:
    assert which("definitely-not-a-command-xyz") is None


def test_process_alive_detects_the_current_process() -> None:
    import os

    assert process_alive(os.getpid()) is True


def test_process_alive_rejects_an_invalid_pid() -> None:
    assert process_alive(0) is False
    assert process_alive(-1) is False


def test_the_summary_is_short() -> None:
    result = CommandResult(("cmd",), 1, "", "boom")
    assert "boom" in result.summary()
