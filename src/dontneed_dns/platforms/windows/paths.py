"""Windows specific filesystem locations.

The Windows edition keeps everything inside the per-user application data
directory so that no administrator rights are required for the normal
operation of the project.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dontneed_dns.core.constants import APP_DIR_NAME

__all__ = [
    "config_dir",
    "config_path",
    "install_dir",
    "local_app_data",
    "log_dir",
    "log_path",
    "pid_file",
    "program_data",
    "roaming_app_data",
    "state_dir",
]


def local_app_data() -> Path:
    """``%LOCALAPPDATA%``."""
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base)
    return Path.home() / "AppData" / "Local"


def roaming_app_data() -> Path:
    """``%APPDATA%``."""
    base = os.environ.get("APPDATA")
    if base:
        return Path(base)
    return Path.home() / "AppData" / "Roaming"


def program_data() -> Path:
    """``%PROGRAMDATA%``."""
    base = os.environ.get("PROGRAMDATA")
    if base:
        return Path(base)
    return Path("C:/ProgramData")


def install_dir() -> Path:
    """The directory the portable build is unpacked into."""
    return local_app_data() / APP_DIR_NAME


def config_dir() -> Path:
    """The directory holding ``config.toml``."""
    return roaming_app_data() / APP_DIR_NAME


def config_path() -> Path:
    """The full path of the configuration file."""
    return config_dir() / "config.toml"


def log_dir() -> Path:
    """The directory holding the log files."""
    return local_app_data() / APP_DIR_NAME / "logs"


def log_path() -> Path:
    """The default log file."""
    return log_dir() / "dontneed-dns.log"


def state_dir() -> Path:
    """The directory holding runtime state such as the PID file."""
    return local_app_data() / APP_DIR_NAME / "state"


def pid_file(name: str = "dontneed-dns") -> Path:
    """The PID file of a component."""
    return state_dir() / f"{name}.pid"


def python_executable() -> str:
    """The interpreter used to run the components."""
    if getattr(sys, "frozen", False):
        return sys.executable
    return sys.executable
