"""Windows Task Scheduler backend.

A scheduled task is the Windows-native way to run a background process for
one user without administrator rights.  It gives start, stop, automatic
startup at logon, a queryable state and a clean removal.

The state is read from the XML definition of the task (``/XML``) and from
the PID file the service runner writes, because the human readable output of
``schtasks`` is localised and therefore not safe to parse.
"""

from __future__ import annotations

import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from pathlib import Path

from dontneed_dns.core.system import CommandResult, run_command, which

__all__ = ["TASK_NAMESPACE", "ScheduledTaskBackend", "TaskInfo"]

TASK_NAMESPACE = "http://schemas.microsoft.com/windows/2004/02/mit/task"


@dataclass(frozen=True)
class TaskInfo:
    """What is known about a scheduled task."""

    exists: bool = False
    enabled: bool | None = None
    running: bool = False
    pid: int | None = None


class ScheduledTaskBackend:
    """Create and control Windows scheduled tasks."""

    def __init__(self, task_prefix: str = "dontneed-dns") -> None:
        self.task_prefix = task_prefix

    # -- helpers ----------------------------------------------------------

    def is_available(self) -> bool:
        """``True`` when ``schtasks.exe`` can be used."""
        return which("schtasks") is not None or which("schtasks.exe") is not None

    def task_name(self, component: str) -> str:
        """The task name of ``component``."""
        return f"{self.task_prefix}-{component}"

    def _run(self, *args: str, timeout: float = 60.0) -> CommandResult:
        return run_command(["schtasks", *args], timeout=timeout)

    # -- state ------------------------------------------------------------

    def exists(self, task_name: str) -> bool:
        """``True`` when the task is registered."""
        return self._run("/Query", "/TN", task_name, timeout=30).ok

    def is_enabled(self, task_name: str) -> bool | None:
        """``True`` when the task is enabled, ``False`` when disabled."""
        result = self._run("/Query", "/TN", task_name, "/XML", timeout=30)
        if not result.ok:
            return None
        try:
            root = ElementTree.fromstring(result.stdout)
        except ElementTree.ParseError:
            return None
        for element in root.iter():
            if element.tag.endswith("Enabled") and element.text is not None:
                return element.text.strip().lower() == "true"
        return None

    def read_pid(self, pid_path: Path) -> int | None:
        """Read a PID file."""
        try:
            value = Path(pid_path).read_text(encoding="utf-8").strip()
        except OSError:
            return None
        try:
            return int(value)
        except ValueError:
            return None

    def info(self, task_name: str, pid_path: Path | None = None) -> TaskInfo:
        """Collect the state of a task."""
        if not self.exists(task_name):
            return TaskInfo(exists=False)
        pid = self.read_pid(pid_path) if pid_path is not None else None
        running = False
        if pid is not None:
            from dontneed_dns.core.system import process_alive

            running = process_alive(pid)
        return TaskInfo(exists=True, enabled=self.is_enabled(task_name), running=running, pid=pid)

    # -- actions ----------------------------------------------------------

    def create(
        self,
        task_name: str,
        command: str,
        *,
        trigger: str = "ONLOGON",
        run_level: str = "LIMITED",
    ) -> CommandResult:
        """Create (or replace) a scheduled task."""
        return self._run(
            "/Create",
            "/TN",
            task_name,
            "/TR",
            command,
            "/SC",
            trigger,
            "/RL",
            run_level,
            "/F",
        )

    def run(self, task_name: str) -> CommandResult:
        """Start the task."""
        return self._run("/Run", "/TN", task_name)

    def end(self, task_name: str) -> CommandResult:
        """Stop the running task."""
        return self._run("/End", "/TN", task_name)

    def enable(self, task_name: str) -> CommandResult:
        """Enable the task."""
        return self._run("/Change", "/TN", task_name, "/ENABLE")

    def disable(self, task_name: str) -> CommandResult:
        """Disable the task."""
        return self._run("/Change", "/TN", task_name, "/DISABLE")

    def delete(self, task_name: str) -> CommandResult:
        """Remove the task."""
        return self._run("/Delete", "/TN", task_name, "/F")
