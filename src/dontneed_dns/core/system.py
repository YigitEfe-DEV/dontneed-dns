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
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from dontneed_dns.core.constants import DEFAULT_COMMAND_TIMEOUT

__all__ = [
    "CommandResult",
    "is_linux",
    "is_root",
    "is_windows",
    "process_alive",
    "run_command",
    "which",
]


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
    return os.geteuid() == 0  # type: ignore[attr-defined]


def is_windows() -> bool:
    """``True`` on Windows regardless of the host Python build."""
    return sys.platform.startswith("win") or os.name == "nt"


def is_linux() -> bool:
    """``True`` on Linux."""
    return sys.platform.startswith("linux")


def process_alive(pid: int) -> bool:
    """Return ``True`` when a process with ``pid`` exists.

    On Windows this uses ``tasklist``; on POSIX it sends signal ``0``.
    """
    if pid <= 0:
        return False
    if is_windows():
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
