"""Smoke test of the terminal user interface."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from dontneed_dns.core.config import config_to_toml, default_config
from dontneed_dns.tui.app import DontneedDnsApp

pytestmark = pytest.mark.integration


def run_app(config_path: Path, actions=()) -> dict[str, str]:
    """Run the interface headless and collect the rendered panels."""
    captured: dict[str, str] = {}

    async def main() -> None:
        app = DontneedDnsApp(str(config_path))
        async with app.run_test() as pilot:
            await pilot.pause()
            for action in actions:
                await action(pilot)
            await pilot.pause()
            for panel in ("dns-panel", "proxy-panel", "checks-panel"):
                captured[panel] = str(app.query_one(f"#{panel}").content)

    asyncio.run(main())
    return captured


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(config_to_toml(default_config()), encoding="utf-8")
    return path


def test_the_interface_starts_and_shows_the_panels(config_path: Path) -> None:
    panels = run_app(config_path)
    assert "DNS resolver" in panels["dns-panel"]
    assert "5354" in panels["dns-panel"]
    assert "Proxy" in panels["proxy-panel"]
    assert "10808" in panels["proxy-panel"]
    assert "Health checks" in panels["checks-panel"]


def test_the_refresh_button_works(config_path: Path) -> None:
    async def press(pilot) -> None:
        await pilot.click("#refresh")
        await pilot.pause()

    panels = run_app(config_path, [press])
    assert "DNS resolver" in panels["dns-panel"]


def test_the_start_button_works(config_path: Path) -> None:
    async def press(pilot) -> None:
        await pilot.click("#start-all")
        await pilot.pause()

    panels = run_app(config_path, [press])
    assert "DNS resolver" in panels["dns-panel"]


def test_the_stop_button_works(config_path: Path) -> None:
    async def press(pilot) -> None:
        await pilot.click("#stop-all")
        await pilot.pause()

    panels = run_app(config_path, [press])
    assert "Proxy" in panels["proxy-panel"]


def test_a_broken_configuration_does_not_crash_the_interface(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[dns]\nlisten_port = 99999\n", encoding="utf-8")
    panels = run_app(path)
    assert panels["dns-panel"]
