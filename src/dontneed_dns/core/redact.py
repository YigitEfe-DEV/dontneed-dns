"""Redaction helpers used by the diagnostic reports.

Diagnostic bundles are meant to be pasted into a bug report, so every value
that could identify a machine or a person is removed:

* absolute home directory paths,
* the current user name,
* host names,
* client IP addresses of local connections,
* anything that looks like a token.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

__all__ = ["Redactor", "redact_text"]

_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPV6_RE = re.compile(r"\b(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}\b")
_TOKEN_RE = re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key|authorization)\b(\s*[:=]\s*)(\S+)")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")


class Redactor:
    """Removes machine identifying data from strings and structures."""

    def __init__(self, *, home: str | Path | None = None, user: str | None = None) -> None:
        resolved_home = str(home) if home is not None else str(Path.home())
        self._home = resolved_home
        self._user = user if user is not None else os.environ.get("USER") or os.environ.get("USERNAME") or ""
        self._hostname = os.uname().nodename if hasattr(os, "uname") else os.environ.get("COMPUTERNAME", "")

    # -- text ------------------------------------------------------------

    def text(self, value: str) -> str:
        """Return ``value`` with sensitive fragments replaced."""
        if not value:
            return value
        out = value
        out = _TOKEN_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}<redacted>", out)
        out = _EMAIL_RE.sub("<email>", out)
        if self._home:
            out = out.replace(self._home, "~")
            # Windows style: C:\Users\name
            out = re.sub(re.escape(self._home).replace(r"\\", r"[\\/]"), "~", out, flags=re.IGNORECASE)
        if self._user:
            out = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(self._user)}(?![A-Za-z0-9_])", "<user>", out)
        if self._hostname:
            out = re.sub(rf"(?<![A-Za-z0-9_.-]){re.escape(self._hostname)}(?![A-Za-z0-9_.-])", "<host>", out)
        # Loopback and link-local addresses stay useful for diagnostics, so
        # only globally routable addresses are masked.
        out = _IPV4_RE.sub(lambda m: _mask_ipv4(m.group(0)), out)
        out = _IPV6_RE.sub(lambda m: _mask_ipv6(m.group(0)), out)
        return out

    # -- structures ------------------------------------------------------

    def value(self, value: Any) -> Any:
        """Recursively redact strings inside dicts, lists and tuples."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {key: self.value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.value(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.value(item) for item in value)
        return value


def _mask_ipv4(address: str) -> str:
    parts = address.split(".")
    if len(parts) != 4:
        return "<ip>"
    if parts[0] == "127":
        return address
    if address.startswith("169.254."):
        return "<link-local>"
    return f"{parts[0]}.x.x.x"


def _mask_ipv6(address: str) -> str:
    lowered = address.lower()
    if lowered in ("::1", "::"):
        return address
    if lowered.startswith("fe80") or lowered.startswith("fc") or lowered.startswith("fd"):
        return "<local>"
    return "<ip>"


def redact_text(value: str, *, home: str | Path | None = None) -> str:
    """Convenience wrapper around :class:`Redactor`."""
    return Redactor(home=home).text(value)
