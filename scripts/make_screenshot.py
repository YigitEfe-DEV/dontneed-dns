#!/usr/bin/env python3
"""Render a screenshot of the terminal interface.

The image is generated from the real interface, not drawn by hand, so the
documentation cannot show something the program does not do.

Usage::

    python scripts/make_screenshot.py docs/tui.svg
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dontneed_dns.tui.app import DontneedDnsApp

SIZE = (100, 32)


async def capture(target: Path) -> None:
    """Start the interface, wait for the first render and save the image."""
    app = DontneedDnsApp()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.pause()
        target.parent.mkdir(parents=True, exist_ok=True)
        app.save_screenshot(str(target))
    print(f"screenshot written to {target}")


def main() -> int:
    """Entry point."""
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs/tui.svg")
    asyncio.run(capture(target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
