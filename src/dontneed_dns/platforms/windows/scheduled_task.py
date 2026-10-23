"""Windows Task Scheduler backend.

A scheduled task is the Windows-native way to run a background process for
one user without administrator rights.  It gives start, stop, automatic
startup at logon, a queryable state and a clean removal.

The task is created from an XML definition instead of from the command line
form of ``schtasks``.  Two reasons:

* ``schtasks /Create /TR "<command>"`` re-parses the command and strips the
  outer quotes, which breaks as soon as the path of the executable or one
  of its arguments contains a space.
* The command line form of the "at log on" trigger is rejected with
  "Access is denied" when the shell is not elevated.  An XML definition
  with a logon trigger for the current user and the ``InteractiveToken``
  logon type is created without any privilege.

The state is read from the XML definition of the task (``/XML``) and from
the PID file the service runner writes, because the human readable output of
``schtasks`` is localised and therefore not safe to parse.
"""

from __future__ import annotations

import os
import tempfile
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from pathlib import Path

from dontneed_dns.core.system import CommandResult, run_command, which

__all__ = [
    "TASK_NAMESPACE",
    "ScheduledTaskBackend",
    "TaskDefinition",
    "TaskInfo",
    "TaskVerification",
    "current_user_id",
]

TASK_NAMESPACE = "http://schemas.microsoft.com/windows/2004/02/mit/task"

#: The delay between the logon and the start of the task.  The network
#: needs a moment before the resolver can reach its providers.
LOGON_DELAY = "PT5S"


@dataclass(frozen=True)
class TaskDefinition:
    """What a scheduled task has to do."""

    command: str
    arguments: str = ""
    user_id: str = ""
    description: str = ""
    trigger_user: str = ""
    delay: str = LOGON_DELAY
    #: The directory the task starts in.  ``python -m`` puts the current
    #: directory at the beginning of ``sys.path``, so this is what makes the
    #: package findable when it is not installed in the interpreter.
    working_directory: str = ""

    def command_line(self) -> str:
        """The command as a single string, for the command line form."""
        if not self.arguments:
            return f'"{self.command}"'
        return f'"{self.command}" {self.arguments}'


@dataclass(frozen=True)
class TaskInfo:
    """What is known about a scheduled task."""

    exists: bool = False
    enabled: bool | None = None
    running: bool = False
    pid: int | None = None


@dataclass(frozen=True)
class TaskVerification:
    """The result of reading a task back and comparing it with the intent."""

    ok: bool
    detail: str = ""


def current_user_id() -> str:
    """The account the current process belongs to.

    The result is used for the logon trigger and for the principal of the
    task, so that the task belongs to the user who created it and needs no
    stored password.
    """
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    domain = os.environ.get("USERDOMAIN") or os.environ.get("USERDNSDOMAIN") or ""
    if not user:
        try:
            import getpass

            user = getpass.getuser()
        except Exception:  # noqa: BLE001 - no account information available
            user = ""
    if not user:
        return ""
    if domain and domain.lower() != user.lower():
        return f"{domain}\\{user}"
    return user


def _escape(value: str) -> str:
    """Escape a value for an XML attribute or element."""
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def build_task_xml(definition: TaskDefinition) -> str:
    """Build the XML definition of a scheduled task."""
    user = _escape(definition.user_id)
    trigger_user = _escape(definition.trigger_user or definition.user_id)
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="{TASK_NAMESPACE}">
  <RegistrationInfo>
    <Author>dontneed-dns</Author>
    <Description>{_escape(definition.description)}</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{trigger_user}</UserId>
      <Delay>{_escape(definition.delay)}</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{_escape(definition.command)}</Command>
      <Arguments>{_escape(definition.arguments)}</Arguments>
      <WorkingDirectory>{_escape(definition.working_directory)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


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

    def query_xml(self, task_name: str) -> str | None:
        """The XML definition of a task, or ``None`` when it cannot be read."""
        result = self._run("/Query", "/TN", task_name, "/XML", timeout=30)
        if not result.ok:
            return None
        return result.stdout

    def is_enabled(self, task_name: str) -> bool | None:
        """``True`` when the task is enabled, ``False`` when disabled."""
        xml = self.query_xml(task_name)
        if xml is None:
            return None
        try:
            root = ElementTree.fromstring(xml)
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

    def create_from_definition(self, task_name: str, definition: TaskDefinition) -> CommandResult:
        """Create (or replace) a scheduled task from an XML definition."""
        xml = build_task_xml(definition)
        descriptor, name = tempfile.mkstemp(suffix=".xml", prefix="dontneed-dns-")
        # The descriptor has to be closed, otherwise Windows keeps the file
        # open and refuses to delete it.
        os.close(descriptor)
        path = Path(name)
        try:
            # Task Scheduler expects the definition in UTF-16.
            path.write_bytes(xml.encode("utf-16"))
            return self._run("/Create", "/TN", task_name, "/XML", str(path), "/F")
        finally:
            try:
                path.unlink()
            except OSError:
                pass

    def create(
        self,
        task_name: str,
        command: str,
        *,
        trigger: str = "ONLOGON",
        run_level: str = "LIMITED",
    ) -> CommandResult:
        """Create (or replace) a scheduled task from the command line form.

        This is the fallback for the XML definition.  ``schtasks`` strips the
        outer quotes of the ``/TR`` value, so the command is wrapped in a
        second pair of quotes.  The form cannot set a working directory.
        """
        return self._run(
            "/Create",
            "/TN",
            task_name,
            "/TR",
            f'"{command}"',
            "/SC",
            trigger,
            "/RL",
            run_level,
            "/F",
        )

    def verify(
        self,
        task_name: str,
        definition: TaskDefinition,
        *,
        check_working_directory: bool = True,
    ) -> TaskVerification:
        """Read the task back and compare it with the intended definition.

        The command line form of ``schtasks`` cannot set a working
        directory, so the check is skipped when that form was used.
        """
        xml = self.query_xml(task_name)
        if xml is None:
            return TaskVerification(False, "the task could not be read back")
        try:
            root = ElementTree.fromstring(xml)
        except ElementTree.ParseError as exc:
            return TaskVerification(False, f"the definition of the task is not valid XML: {exc}")

        command = ""
        arguments = ""
        working_directory = ""
        user = ""
        for element in root.iter():
            tag = element.tag.rsplit("}", 1)[-1]
            if tag == "Command" and element.text:
                command = element.text.strip()
            elif tag == "Arguments" and element.text:
                arguments = element.text.strip()
            elif tag == "WorkingDirectory" and element.text:
                working_directory = element.text.strip()
            elif tag == "UserId" and element.text and not user:
                user = element.text.strip()

        problems: list[str] = []
        if command.lower() != definition.command.lower():
            problems.append(f"the command is {command!r} instead of {definition.command!r}")
        if arguments != definition.arguments:
            problems.append(f"the arguments are {arguments!r} instead of {definition.arguments!r}")
        if definition.user_id and user and user.lower() != definition.user_id.lower():
            problems.append(f"the task belongs to {user!r} instead of {definition.user_id!r}")
        if (
            check_working_directory
            and definition.working_directory
            and working_directory.lower() != definition.working_directory.lower()
        ):
            problems.append(
                f"the working directory is {working_directory!r} instead of {definition.working_directory!r}"
            )

        if problems:
            return TaskVerification(False, "; ".join(problems))
        return TaskVerification(
            True,
            f"the task runs {definition.command_line()} as {user or 'the current user'}",
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
