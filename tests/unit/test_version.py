"""The version is declared in two places and they have to agree."""

from __future__ import annotations

import re
from pathlib import Path

import dontneed_dns

ROOT = Path(__file__).resolve().parent.parent.parent


def test_the_version_of_the_package_matches_the_project_metadata() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match is not None, "pyproject.toml has no version"
    assert match.group(1) == dontneed_dns.__version__
