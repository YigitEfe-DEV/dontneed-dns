"""Safe subprocess execution and privilege helpers.

Every external command in this project is executed through :func:`run_command`
which guarantees:

* no shell interpretation (arguments are passed as a list),
* a bounded runtime,
* captured output,
* a normalised result object instead of a raw ``CompletedProcess``.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from dontneed_dns.core.constants import DEFAULT_COMMAND_TIMEOUT

__all__ = [
    "CommandResult",
    "close_listener",
    "describe_failure",
    "is_linux",
    "is_root",
    "is_windows",
    "port_in_use",
    "process_alive",
    "run_command",
    "udp_port_in_use",
    "which",
]

#: ``SO_EXCLUSIVEADDRUSE`` from ``winsock2.h``.  The constant is not part of
#: the portable ``socket`` module, so the value is kept here as well.
SO_EXCLUSIVEADDRUSE = 0x00008007

#: The exit code Windows reports for a process that is still running.
_STILL_ACTIVE = 259

#: ``PROCESS_QUERY_LIMITED_INFORMATION``; enough to ask for the exit code.
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


@dataclass(frozen=True)
class CommandResult:
    """Outcome of an external command."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        """``True`` when the command exited with status ``0``."""
        return self.returncode == 0 and not self.timed_out

    def summary(self) -> str:
        """A single line description suitable for an error message."""
        if self.timed_out:
            return f"{self.argv[0]} timed out"
        detail = (self.stderr or self.stdout).strip().splitlines()
        tail = detail[-1] if detail else ""
        return f"{self.argv[0]} exited with status {self.returncode}" + (f": {tail}" if tail else "")

    def output(self) -> str:
        """The complete output of the command, stdout and stderr.

        The error of a Windows administration command is often the only
        piece of information that explains what went wrong, so it is
        reported as a whole and not reduced to its last line.
        """
        parts = [part for part in (self.stdout.strip(), self.stderr.strip()) if part]
        return "\n".join(parts)


def describe_failure(result: CommandResult) -> str:
    """Describe a failed command without losing any part of its output.

    The complete output is reported because the last line of an error
    message is often not the part that explains what went wrong.
    """
    output = result.output()
    headline = f"{result.argv[0]} exited with status {result.returncode}"
    if result.timed_out:
        headline = f"{result.argv[0]} timed out"
    if not output:
        return headline
    return f"{headline}\n{output}"


def run_command(
    argv: Sequence[str],
    *,
    timeout: float = DEFAULT_COMMAND_TIMEOUT,
    env: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
    check: bool = False,
) -> CommandResult:
    """Run ``argv`` without a shell and return a :class:`CommandResult`.

    ``shell=True`` is never used, so arguments cannot be interpolated into a
    shell command line.  A timeout is always applied so that a hung
    administration tool cannot freeze the CLI or the TUI.
    """
    args = [str(item) for item in argv]
    if not args:
        raise ValueError("argv must not be empty")

    merged_env: dict[str, str] | None = None
    if env is not None:
        merged_env = {**os.environ, **env}

    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            env=merged_env,
            cwd=str(cwd) if cwd is not None else None,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        result = CommandResult(tuple(args), 124, stdout, stderr, timed_out=True)
    except FileNotFoundError:
        result = CommandResult(tuple(args), 127, "", f"command not found: {args[0]}")
    except PermissionError as exc:
        result = CommandResult(tuple(args), 126, "", str(exc))
    else:
        result = CommandResult(tuple(args), completed.returncode, completed.stdout, completed.stderr)

    if check and not result.ok:
        raise RuntimeError(result.summary())
    return result


def which(name: str) -> str | None:
    """Return the absolute path of ``name`` or ``None`` when it is absent."""
    return shutil.which(name)


def is_root() -> bool:
    """``True`` when the current process runs with root privileges."""
    if is_windows():
        return False
    return os.geteuid() == 0


def is_windows() -> bool:
    """``True`` on Windows regardless of the host Python build."""
    return sys.platform.startswith("win") or os.name == "nt"


def is_linux() -> bool:
    """``True`` on Linux."""
    return sys.platform.startswith("linux")


def process_alive(pid: int) -> bool:
    """Return ``True`` when a process with ``pid`` exists.

    On Windows the kernel is asked directly, so the answer does not depend on
    ``tasklist`` being installed and on the language of its output.  Only when
    the kernel call is not available the external command is used.
    """
    if pid <= 0:
        return False
    if is_windows():
        alive = _windows_process_alive(pid)
        if alive is not None:
            return alive
        result = run_command(["tasklist", "/FI", f"PID eq {pid}", "/NH"], timeout=10)
        return result.ok and str(pid) in result.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _windows_process_alive(pid: int) -> bool | None:
    """Ask the Windows kernel whether the process exists.

    Returns ``None`` when the question cannot be answered, so that the caller
    can fall back to ``tasklist``.  A process that exists but belongs to
    another account is reported as alive: it cannot be opened for the query,
    but it is certainly running.
    """
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:  # noqa: BLE001 - not Windows
        return None

    try:
        # ``WinDLL`` only exists on Windows.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - no kernel32
        return None

    try:
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
    except Exception:  # noqa: BLE001 - defensive
        return None

    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # ``get_last_error`` only exists on Windows.
        error = ctypes.get_last_error()  # type: ignore[attr-defined]
        if error == 87:  # ERROR_INVALID_PARAMETER: no such process
            return False
        if error == 5:  # ERROR_ACCESS_DENIED: it exists, it is not ours
            return True
        return None

    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return None
        return code.value == _STILL_ACTIVE
    except Exception:  # noqa: BLE001 - defensive
        return None
    finally:
        kernel32.CloseHandle(handle)


def close_listener(sock: socket.socket) -> None:
    """Close a listening socket and release its port immediately.

    A thread that is blocked in ``accept()`` keeps the socket alive in the
    kernel, so closing it is not enough: the port would stay bound until
    the next connection arrives.  Shutting the socket down first wakes the
    blocked call up.
    """
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


def port_in_use(host: str, port: int, *, timeout: float = 0.5) -> bool:
    """Return ``True`` when something already accepts connections on the address.

    Windows allows two sockets to bind the same address because
    ``SO_REUSEADDR`` is not as restrictive there as on Linux, so a second
    instance would silently take the port over.  Connecting to the address
    detects that situation on every platform.
    """
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            probe.settimeout(timeout)
            probe.connect((host, port))
    except OSError:
        return False
    return True


def udp_port_in_use(host: str, port: int) -> bool:
    """Return ``True`` when a datagram socket cannot claim the address.

    A datagram socket cannot be probed by connecting to it, so the conflict
    is found by trying to bind the address.  The probe is deliberately
    *exclusive*: it does not ask for the address to be shared, so that it
    fails no matter how the socket that holds the port was created.  On
    Windows that means ``SO_EXCLUSIVEADDRUSE``, which refuses the bind even
    when the other socket used ``SO_REUSEADDR``.

    Datagram sockets never enter the ``TIME_WAIT`` state, so the probe
    cannot report a port that is only waiting to expire.
    """
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_DGRAM)
    try:
        if is_windows():
            try:
                sock.setsockopt(socket.SOL_SOCKET, SO_EXCLUSIVEADDRUSE, 1)
            except OSError:
                # The option is not supported by this build; the plain bind
                # still refuses a port that is held by another socket.
                pass
        sock.bind((host, port))
    except OSError:
        return True
    finally:
        sock.close()
    return False
