"""Entry point of the self contained Fedora edition executable.

The executable is the application and the installer in one file:

* without arguments it installs the application, unless it already runs
  from the installed copy,
* ``--install``, ``--uninstall``, ``--status``, ``--version`` and
  ``--help`` drive the installer,
* every other argument is handed to the application CLI, so the released
  file can be used exactly like the installed one.
"""

from __future__ import annotations

import sys
from pathlib import Path

INSTALLER_FLAGS = ("--install", "--uninstall", "--status", "--version", "-V", "--help", "-h")


def _installed_copy() -> Path:
    """The path of the executable the installer places in the bin directory."""
    from dontneed_dns.platforms.linux.adapter import INSTALLED_BINARY_NAME
    from dontneed_dns.core.paths import bin_dir

    return bin_dir() / INSTALLED_BINARY_NAME


def _running_as_installed_copy() -> bool:
    """``True`` when this process is the copy the installer placed."""
    try:
        running = Path(sys.executable).resolve()
    except OSError:
        return False
    try:
        installed = _installed_copy().resolve()
    except OSError:
        return False
    return running == installed


def main() -> int:
    """Dispatch to the installer or to the application."""
    from dontneed_dns.core.paths import bin_dir
    from dontneed_dns.platforms.linux.adapter import INSTALLED_BINARY_NAME

    arguments = sys.argv[1:]
    installed_copy = _running_as_installed_copy()

    if arguments and arguments[0] in INSTALLER_FLAGS:
        if arguments[0] in ("--help", "-h") and installed_copy:
            # The installed copy behaves like the application.
            from dontneed_dns.cli.main import main as cli_main

            cli_main()
            return 0
        from dontneed_dns.installer import main as installer_main

        return installer_main(arguments)

    if not arguments:
        if installed_copy:
            from dontneed_dns.cli.main import main as cli_main

            cli_main()
            return 0
        from dontneed_dns.installer import main as installer_main

        return installer_main([])

    from dontneed_dns.cli.main import main as cli_main

    cli_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
