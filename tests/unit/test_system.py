"""Tests of the safe subprocess helpers."""

from __future__ import annotations

import socket
import sys

from dontneed_dns.core.system import (
    CommandResult,
    port_in_use,
    process_alive,
    run_command,
    udp_port_in_use,
    which,
)


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


# -- the port probes --------------------------------------------------------


def test_a_bound_udp_port_is_reported_as_used() -> None:
    """A port that a datagram socket holds must be found."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as holder:
        holder.bind(("127.0.0.1", 0))
        port = holder.getsockname()[1]
        assert udp_port_in_use("127.0.0.1", port) is True


def test_a_released_udp_port_is_not_reported_as_used() -> None:
    """A port that nobody holds must not be reported."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as holder:
        holder.bind(("127.0.0.1", 0))
        port = holder.getsockname()[1]

    assert udp_port_in_use("127.0.0.1", port) is False


def test_the_udp_probe_finds_a_port_that_only_holds_datagrams() -> None:
    """A conflict on the datagram port alone has to be found.

    The resolver needs the port for TCP and for UDP at the same time.  A
    program that holds only the datagram port breaks the resolver just as
    much, and the connection probe of the TCP port cannot see it.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as holder:
        holder.bind(("127.0.0.1", 0))
        port = holder.getsockname()[1]

        assert port_in_use("127.0.0.1", port) is False
        assert udp_port_in_use("127.0.0.1", port) is True


def test_a_port_nobody_holds_is_not_reported_as_used() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    assert port_in_use("127.0.0.1", port) is False


# -- the process probe ------------------------------------------------------


def test_process_alive_detects_a_process_that_does_not_exist() -> None:
    """A process id that was never used must not be reported as alive."""
    # The highest process id is far above the number of processes a machine
    # can hold, so it cannot exist.
    assert process_alive(0x7FFFFFF0) is False
