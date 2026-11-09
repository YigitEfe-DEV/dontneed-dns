"""Logging configuration with optional redaction.

The project logs what an operator needs to debug a connection problem and
nothing more.  Domain names are logged because they are the subject of the
request; client addresses are not, and every message passes through the
:class:`~dontneed_dns.core.redact.Redactor` when redaction is enabled.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import TextIO

from dontneed_dns.core.config import Config
from dontneed_dns.core.paths import log_path
from dontneed_dns.core.redact import Redactor

__all__ = ["LOGGER_NAME", "get_logger", "setup_logging"]

LOGGER_NAME = "dontneed_dns"

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class _RedactingFilter(logging.Filter):
    """Applies the :class:`Redactor` to every formatted record."""

    def __init__(self, redactor: Redactor) -> None:
        super().__init__()
        self._redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - defensive
            return True
        redacted = self._redactor.text(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


class _SafeStreamHandler(logging.StreamHandler):
    """A stream handler that survives a console that cannot be written to.

    A scheduled task and a windowed executable run without a console.  The
    stream of such a process is either absent or an invalid handle, and the
    default error handling of the logging system would raise while it tries
    to report the original problem.  The log file is the reliable sink, so
    the console is allowed to fail silently.
    """

    def handleError(self, record: logging.LogRecord) -> None:
        """Never raise from the logging system."""


def _console_stream() -> TextIO | None:
    """Return a usable console stream, or ``None`` when there is none."""
    for candidate in (sys.stdout, sys.stderr):
        if candidate is not None:
            return candidate
    return None


def setup_logging(config: Config, *, verbose: bool = False, quiet: bool = False) -> logging.Logger:
    """Configure and return the project logger.

    A log file is always written.  When the configuration does not name one,
    the platform default is used: the file in the per-user state directory on
    Linux and the file in the per-user application data directory on
    Windows.  Without it the Windows edition would have no log at all,
    because a scheduled task has no console to write to.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.handlers.clear()
    logger.propagate = False

    level = logging.DEBUG if verbose else logging.INFO
    if quiet:
        level = logging.ERROR
    logger.setLevel(level)

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    stream = _console_stream()
    if stream is not None:
        stream_handler = _SafeStreamHandler(stream)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

    log_file = Path(config.logging.file) if config.logging.file else log_path()
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError as exc:
        logger.warning("log file %s could not be opened: %s", log_file, exc.strerror or exc)

    if config.logging.redact:
        redactor = Redactor()
        for handler in logger.handlers:
            handler.addFilter(_RedactingFilter(redactor))

    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a child logger of the project logger."""
    if not name:
        return logging.getLogger(LOGGER_NAME)
    return logging.getLogger(f"{LOGGER_NAME}.{name}")
