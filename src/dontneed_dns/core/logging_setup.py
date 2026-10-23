"""Logging configuration with optional redaction.

The project logs what an operator needs to debug a connection problem and
nothing more.  Domain names are logged because they are the subject of the
request; client addresses are not, and every message passes through the
:class:`~dontneed_dns.core.redact.Redactor` when redaction is enabled.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from dontneed_dns.core.config import Config
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


def setup_logging(config: Config, *, verbose: bool = False, quiet: bool = False) -> logging.Logger:
    """Configure and return the project logger."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.handlers.clear()
    logger.propagate = False

    level = logging.DEBUG if verbose else logging.INFO
    if quiet:
        level = logging.ERROR
    logger.setLevel(level)

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    if config.logging.file:
        log_file = Path(config.logging.file)
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
            )
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except OSError as exc:
            logger.warning("log file could not be opened: %s", exc.strerror)

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
