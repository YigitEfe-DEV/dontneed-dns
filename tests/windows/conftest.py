"""Shared fixtures of the Windows edition tests.

The fake ``schtasks`` keeps the definition of every task it creates, so a
test can check what a task really runs instead of only checking the
arguments of the command line.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dontneed_dns.core.system import CommandResult

TASK_NAMESPACE = "http://schemas.microsoft.com/windows/2004/02/mit/task"

ACCESS_DENIED = "ERROR: Access is denied."


def build_xml(command: str, arguments: str = "", user: str = "") -> str:
    """Build a task definition like the Task Scheduler exports it."""
    if not user:
        from dontneed_dns.platforms.windows.scheduled_task import current_user_id

        user = current_user_id()
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="{TASK_NAMESPACE}">
  <RegistrationInfo>
    <Author>dontneed-dns</Author>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{user}</UserId>
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
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      <Arguments>{arguments}</Arguments>
    </Exec>
  </Actions>
</Task>
"""


class FakeSchtasks:
    """A fake ``schtasks`` that stores the tasks it is asked to create."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.answers: dict[str, CommandResult] = {}
        self.default = CommandResult(("",), 0, "", "")
        self.tasks: dict[str, str] = {}
        self.fail_xml_creation = False
        self.fail_command_creation = False
        self.creation_error = ACCESS_DENIED
        #: The XML files that were handed to ``schtasks``.
        self.xml_files: list[Path] = []

    def __call__(self, argv, *, timeout=60.0, **kwargs):
        self.calls.append(list(argv))
        joined = " ".join(argv)

        if "/Create" in argv:
            return self._create(argv)
        if "/Query" in argv:
            return self._query(argv)
        if "/Delete" in argv:
            name = argv[argv.index("/TN") + 1]
            self.tasks.pop(name, None)
            return CommandResult(("",), 0, "SUCCESS", "")
        for needle, result in self.answers.items():
            if needle in joined:
                return result
        return self.default

    def _create(self, argv: list[str]) -> CommandResult:
        name = argv[argv.index("/TN") + 1]
        if "/XML" in argv:
            if self.fail_xml_creation:
                return CommandResult(("",), 1, "", self.creation_error)
            path = Path(argv[argv.index("/XML") + 1])
            self.xml_files.append(path)
            xml = path.read_bytes().decode("utf-16")
        else:
            if self.fail_command_creation:
                return CommandResult(("",), 1, "", self.creation_error)
            raw = argv[argv.index("/TR") + 1]
            if raw.startswith('"') and raw.endswith('"'):
                # schtasks strips one pair of quotes.
                raw = raw[1:-1]
            if raw.startswith('"'):
                end = raw.index('"', 1)
                command = raw[1:end]
                arguments = raw[end + 1 :].strip()
            else:
                command, _, arguments = raw.partition(" ")
            xml = build_xml(command, arguments)
        self.tasks[name] = xml
        return CommandResult(("",), 0, "SUCCESS: The scheduled task was created.", "")

    def _query(self, argv: list[str]) -> CommandResult:
        name = argv[argv.index("/TN") + 1]
        if name not in self.tasks:
            return CommandResult(("",), 1, "", "ERROR: The system cannot find the file specified.")
        if "/XML" in argv:
            return CommandResult(("",), 0, self.tasks[name], "")
        return CommandResult(("",), 0, f"TaskName: {name}\nStatus: Ready\n", "")

    # -- helpers ----------------------------------------------------------

    def called(self, needle: str) -> bool:
        """``True`` when a call contains ``needle``."""
        return any(needle in " ".join(call) for call in self.calls)

    def call_with(self, needle: str) -> list[str] | None:
        """The first call that contains ``needle``."""
        for call in self.calls:
            if needle in " ".join(call):
                return call
        return None


@pytest.fixture
def schtasks(monkeypatch: pytest.MonkeyPatch) -> FakeSchtasks:
    """Replace ``schtasks`` with a fake that stores the created tasks."""
    from dontneed_dns.platforms.windows import scheduled_task as module

    fake = FakeSchtasks()
    monkeypatch.setattr(module, "run_command", fake)
    return fake
