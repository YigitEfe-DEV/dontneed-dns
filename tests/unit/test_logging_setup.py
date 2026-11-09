"""Tests of the logging configuration.

The Windows edition has no journal and a scheduled task has no console, so
the log file is the only place where the operator can read what happened.
The tests make sure that a log file is always written.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

import pytest

from dontneed_dns.core import logging_setup
from dontneed_dns.core.config import Config, LoggingConfig


@pytest.fixture
def log_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The path of the log file the logger is expected to write."""
    path = tmp_path / "logs" / "dontneed-dns.log"
    monkeypatch.setattr(logging_setup, "log_path", lambda: path)
    return path


def _flush_the_file_handler(logger: logging.Logger) -> None:
    """Flush the handler that writes the log file.

    The console handler may be broken on purpose, so it is not flushed.
    """
    for handler in logger.handlers:
        if isinstance(handler, logging.handlers.RotatingFileHandler):
            handler.flush()


def test_a_log_file_is_written_by_default(log_file: Path) -> None:
    """Without an explicit file the platform default is used.

    The configuration leaves the file empty, so the default of the platform
    is what decides where the log goes.  Without it the Windows edition
    would have no log at all.
    """
    logger = logging_setup.setup_logging(Config())

    logger.info("a line for the log file")

    _flush_the_file_handler(logger)
    assert log_file.exists()
    assert "a line for the log file" in log_file.read_text(encoding="utf-8")


def test_the_log_file_can_be_configured(tmp_path: Path) -> None:
    path = tmp_path / "somewhere" / "custom.log"
    config = Config(logging=LoggingConfig(file=str(path)))

    logger = logging_setup.setup_logging(config)
    logger.info("a configured line")

    _flush_the_file_handler(logger)
    assert path.exists()
    assert "a configured line" in path.read_text(encoding="utf-8")


def test_the_logger_survives_a_console_that_cannot_be_written_to(
    log_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scheduled task and a windowed executable have no console.

    Without a usable stream the logging system would raise while it tries
    to report the original problem, and the component would die instead of
    writing to the log file.
    """
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    logger = logging_setup.setup_logging(Config())
    logger.info("a line without a console")

    _flush_the_file_handler(logger)
    assert "a line without a console" in log_file.read_text(encoding="utf-8")


def test_a_closed_console_does_not_break_the_logging(log_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A stream that raises on write must not take the component down."""

    class BrokenStream:
        def write(self, _text: str) -> int:
            raise OSError("the console is gone")

        def flush(self) -> None:
            raise OSError("the console is gone")

    monkeypatch.setattr(sys, "stdout", BrokenStream())
    monkeypatch.setattr(sys, "stderr", BrokenStream())

    logger = logging_setup.setup_logging(Config())
    # The message is lost for the console, but it must not raise.
    logger.info("a line with a broken console")

    _flush_the_file_handler(logger)
    assert "a line with a broken console" in log_file.read_text(encoding="utf-8")


def test_the_messages_are_redacted(log_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USERNAME", "Excalibur")
    monkeypatch.delenv("USER", raising=False)

    logger = logging_setup.setup_logging(Config(logging=LoggingConfig(redact=True)))
    logger.info("the user is Excalibur")

    _flush_the_file_handler(logger)
    content = log_file.read_text(encoding="utf-8")
    assert "Excalibur" not in content
    assert "<user>" in content
