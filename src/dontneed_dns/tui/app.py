"""The terminal user interface.

The interface shows the same information on both platforms: the state of
every component, the addresses they listen on, the validity of the
configuration, the health checks and the most recent log lines.  The
Start/Stop/Restart buttons use the platform adapter, so they never run an
incompatible command.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Footer, Header, Static

from dontneed_dns import __version__
from dontneed_dns.core.capability import CapabilityLevel
from dontneed_dns.core.config import Config, load_config
from dontneed_dns.core.errors import ConfigError, UnsupportedPlatformError
from dontneed_dns.core.health import run_health_checks
from dontneed_dns.core.redact import Redactor
from dontneed_dns.platforms import create_adapter
from dontneed_dns.platforms.base import Component, PlatformAdapter

__all__ = ["DontneedDnsApp", "run_tui"]

REFRESH_SECONDS = 5.0
LOG_LINES = 12


class DontneedDnsApp(App):
    """The dontneed-dns dashboard."""

    TITLE = "dontneed-dns"
    SUB_TITLE = f"v{__version__}"

    CSS = """
    Screen {
        layout: vertical;
    }

    #toolbar {
        height: 3;
        padding: 0 1;
        background: $panel;
    }

    #toolbar Button {
        margin: 0 1 0 0;
        min-width: 10;
    }

    #panels {
        height: 1fr;
    }

    #left {
        width: 50%;
        padding: 0 1;
    }

    #right {
        width: 50%;
        padding: 0 1;
    }

    .panel {
        border: round $primary;
        padding: 0 1;
        margin-bottom: 1;
        height: auto;
    }

    #dns-panel { height: 9; }
    #proxy-panel { height: 9; }
    #checks-panel { height: 15; }
    #logs-panel { height: 1fr; }

    #notice {
        height: auto;
        padding: 0 1;
        color: $warning;
    }
    """

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
        ("ctrl+s", "start_all", "Start all"),
        ("ctrl+x", "stop_all", "Stop all"),
    ]

    def __init__(self, config_path: str | None = None) -> None:
        super().__init__()
        self.config_path = config_path
        self.config: Config | None = None
        self.adapter: PlatformAdapter | None = None
        self._load_error: str = ""
        # The interface can be shown on a shared screen, so everything that
        # identifies the machine is removed before it is drawn.
        self._redactor = Redactor()

    # -- data -------------------------------------------------------------

    def _load(self) -> None:
        try:
            self.config = load_config(self.config_path)
        except ConfigError as exc:
            self._load_error = f"configuration error: {exc}"
            self.config = None
            return
        try:
            self.adapter = create_adapter()
        except UnsupportedPlatformError as exc:
            self._load_error = str(exc)
            self.adapter = None
            return
        self._load_error = ""

    def _component_panel(self, component: Component) -> str:
        if self.adapter is None or self.config is None:
            return f"{component.label}\n\nunavailable"

        state = self.adapter.status(component)
        enabled = self.adapter.is_enabled(component)
        if component is Component.DNS:
            host, port = self.config.dns.listen_host, self.config.dns.listen_port
            extra = f"split domains: {', '.join(self.config.dns.split_domains[:3])}"
        else:
            host, port = self.config.proxy.listen_host, self.config.proxy.listen_port
            fragmentation = "on" if self.config.proxy.fragmentation_enabled else "off"
            extra = f"TLS fragmentation (experimental): {fragmentation}"

        autostart = "unknown" if enabled is None else ("yes" if enabled else "no")
        lines = [
            f"{component.label}",
            f"state          {state.label}",
            f"listening on   {host}:{port}",
            f"starts automatically  {autostart}",
            extra,
        ]
        return "\n".join(lines)

    def _checks_panel(self) -> str:
        if self.config is None:
            return "Health checks\n\nunavailable"
        checks = run_health_checks(self.config, timeout=2.0)
        lines = ["Health checks", ""]
        for check in checks:
            marker = "OK  " if check.ok else ("WARN" if check.severity == "warning" else "FAIL")
            lines.append(f"[{marker}] {check.name}: {check.detail}")
        return "\n".join(lines)

    def _logs_panel(self) -> str:
        if self.adapter is None:
            return "Recent logs\n\nunavailable"
        lines = ["Recent logs", ""]
        for component in Component:
            result = self.adapter.logs(component, lines=LOG_LINES)
            lines.append(f"-- {component.label} --")
            if not result.ok:
                lines.append(self._redactor.text(result.message))
            else:
                output = self._redactor.text(str(result.data.get("output", ""))).strip()
                lines.extend(output.splitlines()[-LOG_LINES:] if output else ["(no output)"])
            lines.append("")
        return "\n".join(lines)

    def _platform_notices(self) -> list[str]:
        if self.adapter is None:
            return []
        notices: list[str] = []
        matrix = self.adapter.capabilities()
        for entry in matrix.entries:
            if entry.level is CapabilityLevel.UNSUPPORTED and entry.feature.value in (
                "dns_interception",
                "network_configuration",
            ):
                notices.append(f"{entry.feature.value.replace('_', ' ')} is not available on this platform.")
        return notices

    # -- composition ------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="toolbar"):
            yield Button("Refresh", id="refresh", variant="primary")
            yield Button("Start all", id="start-all", variant="success")
            yield Button("Stop all", id="stop-all", variant="warning")
            yield Button("Restart DNS", id="restart-dns")
            yield Button("Restart proxy", id="restart-proxy")
        with Horizontal(id="panels"):
            with Vertical(id="left"):
                yield Static(self._component_panel(Component.DNS), id="dns-panel", classes="panel")
                yield Static(self._component_panel(Component.PROXY), id="proxy-panel", classes="panel")
                yield Static("", id="notice")
            with Vertical(id="right"):
                yield Static(self._checks_panel(), id="checks-panel", classes="panel")
                with VerticalScroll(id="logs-panel", classes="panel"):
                    yield Static("", id="logs-content")
        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(REFRESH_SECONDS, self.refresh_panels)
        self.refresh_panels()

    # -- actions ----------------------------------------------------------

    def refresh_panels(self) -> None:
        """Reload the state and redraw every panel.

        The method is deliberately not called ``refresh``: Textual uses that
        name for its own repainting primitive.
        """
        self._load()
        if self.config is None or self.adapter is None:
            notice = self._load_error or "the platform is not supported"
            self.query_one("#dns-panel", Static).update(notice)
            self.query_one("#proxy-panel", Static).update("")
            self.query_one("#checks-panel", Static).update("")
            self.query_one("#logs-content", Static).update("")
            return

        self.query_one("#dns-panel", Static).update(self._component_panel(Component.DNS))
        self.query_one("#proxy-panel", Static).update(self._component_panel(Component.PROXY))
        self.query_one("#checks-panel", Static).update(self._checks_panel())
        self.query_one("#logs-content", Static).update(self._logs_panel())

        notices = self._platform_notices()
        self.query_one("#notice", Static).update("\n".join(notices))

    def _apply(self, action: str, component: Component | None = None) -> None:
        if self.adapter is None:
            return
        try:
            if component is None:
                for target in Component:
                    getattr(self.adapter, action)(target)
            else:
                getattr(self.adapter, action)(component)
        except Exception as exc:  # noqa: BLE001 - the TUI must not crash
            self.notify(f"The operation failed: {exc}", severity="error")
        self.refresh_panels()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle the toolbar buttons."""
        button_id = event.button.id or ""
        if button_id == "refresh":
            self.refresh_panels()
        elif button_id == "start-all":
            self._apply("start")
        elif button_id == "stop-all":
            self._apply("stop")
        elif button_id == "restart-dns":
            self._apply("restart", Component.DNS)
        elif button_id == "restart-proxy":
            self._apply("restart", Component.PROXY)

    def action_refresh(self) -> None:
        """Refresh every panel."""
        self.refresh_panels()

    def action_start_all(self) -> None:
        """Start both components."""
        self._apply("start")

    def action_stop_all(self) -> None:
        """Stop both components."""
        self._apply("stop")


def run_tui(config_path: str | None = None) -> None:
    """Run the terminal user interface."""
    DontneedDnsApp(config_path).run()
