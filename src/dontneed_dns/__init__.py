"""dontneed-dns - cross-platform DNS-over-HTTPS and TLS-fragmenting proxy manager.

The package is intentionally split into a portable ``core`` and two
first-class platform editions (``platforms.linux`` / ``platforms.windows``).
Shared modules never invoke platform administration commands directly; they
talk to a :class:`dontneed_dns.platforms.base.PlatformAdapter`.
"""

from __future__ import annotations

__all__ = ["PROJECT_NAME", "USER_AGENT", "__version__"]

__version__ = "0.1.0"

PROJECT_NAME = "dontneed-dns"

USER_AGENT = f"{PROJECT_NAME}/{__version__}"
