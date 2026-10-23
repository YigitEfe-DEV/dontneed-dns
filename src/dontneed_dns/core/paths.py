"""Per-user filesystem locations.

Only *user scoped* directories are used for configuration and data.  The
project never writes into system directories on its own; the installers copy
files into the places the platform expects and record what they changed so it
can be rolled back.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dontneed_dns.core.constants import APP_DIR_NAME, CONFIG_FILE_NAME, LOG_FILE_NAME

__all__ = [
    "config_dir",
    "config_path",
    "data_dir",
    "log_dir",
    "log_path",
    "pid_file",
    "state_dir",
]


def _home() -> Path:
    return Path.home()


def config_dir() -> Path:
    """The directory holding ``config.toml``."""
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA")
        root = Path(base) if base else _home() / "AppData" / "Roaming"
        return root / APP_DIR_NAME
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else _home() / ".config"
    return root / APP_DIR_NAME


def config_path() -> Path:
    """The full path of the configuration file."""
    return config_dir() / CONFIG_FILE_NAME


def data_dir() -> Path:
    """The directory holding runtime data such as the PID file."""
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else _home() / "AppData" / "Local"
        return root / APP_DIR_NAME
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else _home() / ".local" / "state"
    return root / APP_DIR_NAME


def state_dir() -> Path:
    """Alias of :func:`data_dir` kept for readability at call sites."""
    return data_dir()


def log_dir() -> Path:
    """The directory holding log files."""
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else _home() / "AppData" / "Local"
        return root / APP_DIR_NAME / "logs"
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else _home() / ".local" / "state"
    return root / APP_DIR_NAME / "logs"


def log_path() -> Path:
    """The default log file."""
    return log_dir() / LOG_FILE_NAME


def pid_file(name: str = "dontneed-dns") -> Path:
    """The PID file for a component."""
    return data_dir() / f"{name}.pid"
