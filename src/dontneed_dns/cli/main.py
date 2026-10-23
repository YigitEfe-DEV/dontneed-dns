"""The command line interface.

The command surface is identical on Linux and Windows.  Every command works
without an interactive terminal so the tool can be used from scripts and
from the GitHub Actions workflows.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from dontneed_dns import __version__
from dontneed_dns.core.config import (
    Config,
    config_to_toml,
    default_config,
    load_config,
    save_config,
)
from dontneed_dns.core.errors import ConfigError, UnsupportedPlatformError
from dontneed_dns.core.health import Severity, run_health_checks
from dontneed_dns.core.redact import Redactor
from dontneed_dns.platforms import create_adapter
from dontneed_dns.platforms.base import Component, OperationResult, PlatformAdapter, ServiceState

__all__ = ["app", "main"]

console = Console()
error_console = Console(stderr=True)

app = typer.Typer(
    name="dontneed-dns",
    help="Cross-platform DNS-over-HTTPS and proxy management for Discord connectivity.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-V", help="Show the version and exit."),
) -> None:
    """Print the version and exit."""
    if version:
        console.print(f"dontneed-dns {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())


dns_app = typer.Typer(name="dns", help="Manage the DNS resolver.", no_args_is_help=True)
proxy_app = typer.Typer(name="proxy", help="Manage the proxy.", no_args_is_help=True)
config_app = typer.Typer(name="config", help="Inspect and validate the configuration.", no_args_is_help=True)

app.add_typer(dns_app)
app.add_typer(proxy_app)
app.add_typer(config_app)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_CONFIG = 2
EXIT_UNAVAILABLE = 3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _command_name() -> str:
    """The name the command was started with."""
    return Path(sys.argv[0]).name if sys.argv and sys.argv[0] else "dontneed-dns"


def _load(config_path: str | None = None) -> tuple[Config, PlatformAdapter]:
    """Load the configuration and create the platform adapter."""
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        error_console.print(f"[red]Configuration error:[/red] {exc}")
        raise typer.Exit(EXIT_CONFIG) from exc

    try:
        adapter = create_adapter()
    except UnsupportedPlatformError as exc:
        error_console.print(f"[red]Unsupported platform:[/red] {exc}")
        raise typer.Exit(EXIT_UNAVAILABLE) from exc
    return config, adapter


def _state_label(state: ServiceState) -> str:
    colours = {
        ServiceState.RUNNING: "green",
        ServiceState.STOPPED: "yellow",
        ServiceState.FAILED: "red",
        ServiceState.NOT_INSTALLED: "dim",
        ServiceState.INSTALLED: "cyan",
        ServiceState.UNKNOWN: "dim",
    }
    return f"[{colours.get(state, 'white')}]{state.label}[/]"


def _print_result(result: OperationResult) -> int:
    if result.ok:
        console.print(f"[green]OK[/] {result.message}")
        return EXIT_OK
    if result.requires_elevation:
        error_console.print(f"[yellow]Elevation required:[/yellow] {result.message}")
        if result.detail:
            error_console.print(f"  {result.detail}")
        return EXIT_ERROR
    if result.unsupported:
        error_console.print(f"[yellow]Not supported on this platform:[/yellow] {result.message}")
        return EXIT_UNAVAILABLE
    error_console.print(f"[red]Failed:[/red] {result.message}")
    if result.detail:
        error_console.print(f"  {result.detail}")
    return EXIT_ERROR


def _component_table(config: Config, adapter: PlatformAdapter) -> Table:
    table = Table(title="dontneed-dns", title_justify="left", show_header=True, header_style="bold")
    table.add_column("Component")
    table.add_column("State")
    table.add_column("Starts automatically")
    table.add_column("Address")

    for component, host, port in (
        (Component.DNS, config.dns.listen_host, config.dns.listen_port),
        (Component.PROXY, config.proxy.listen_host, config.proxy.listen_port),
    ):
        state = adapter.status(component)
        enabled = adapter.is_enabled(component)
        if enabled is None:
            enabled_text = "[dim]unknown[/]"
        elif enabled:
            enabled_text = "[green]yes[/]"
        else:
            enabled_text = "[yellow]no[/]"
        table.add_row(component.label, _state_label(state), enabled_text, f"{host}:{port}")
    return table


def _run_action(
    component: Component,
    action: str,
    config_path: str | None,
    scope: str | None,
) -> int:
    _config, adapter = _load(config_path)
    if scope:
        adapter.scope = scope

    if action == "status":
        state = adapter.status(component)
        enabled = adapter.is_enabled(component)
        console.print(f"{component.label}: {_state_label(state)}")
        autostart = "unknown" if enabled is None else ("yes" if enabled else "no")
        console.print(f"Starts automatically: {autostart}")
        return EXIT_OK if state is not ServiceState.FAILED else EXIT_ERROR

    if action == "logs":
        result = adapter.logs(component, lines=50)
        if not result.ok:
            return _print_result(result)
        output = str(result.data.get("output", ""))
        if output.strip():
            console.print(output, markup=False, highlight=False)
        else:
            console.print("[dim]no log output[/]")
        return EXIT_OK

    handler = {
        "start": adapter.start,
        "stop": adapter.stop,
        "restart": adapter.restart,
        "enable": adapter.enable,
        "disable": adapter.disable,
    }.get(action)
    if handler is None:
        error_console.print(f"[red]Unknown action:[/red] {action}")
        return EXIT_ERROR
    return _print_result(handler(component))


def _add_component_commands(sub_app: typer.Typer, component: Component) -> None:
    """Register the start/stop/restart/status/logs commands of a component."""

    @sub_app.command("start", help=f"Start the {component.label.lower()}.")
    def start(
        config_path: str | None = typer.Option(None, "--config", help="Path of the configuration file."),
        scope: str | None = typer.Option(None, "--scope", help="Service scope: user or system."),
    ) -> None:
        raise typer.Exit(_run_action(component, "start", config_path, scope))

    @sub_app.command("stop", help=f"Stop the {component.label.lower()}.")
    def stop(
        config_path: str | None = typer.Option(None, "--config", help="Path of the configuration file."),
        scope: str | None = typer.Option(None, "--scope", help="Service scope: user or system."),
    ) -> None:
        raise typer.Exit(_run_action(component, "stop", config_path, scope))

    @sub_app.command("restart", help=f"Restart the {component.label.lower()}.")
    def restart(
        config_path: str | None = typer.Option(None, "--config", help="Path of the configuration file."),
        scope: str | None = typer.Option(None, "--scope", help="Service scope: user or system."),
    ) -> None:
        raise typer.Exit(_run_action(component, "restart", config_path, scope))

    @sub_app.command("status", help=f"Show the state of the {component.label.lower()}.")
    def status(
        config_path: str | None = typer.Option(None, "--config", help="Path of the configuration file."),
        scope: str | None = typer.Option(None, "--scope", help="Service scope: user or system."),
    ) -> None:
        raise typer.Exit(_run_action(component, "status", config_path, scope))

    @sub_app.command("enable", help=f"Start the {component.label.lower()} automatically.")
    def enable(
        config_path: str | None = typer.Option(None, "--config", help="Path of the configuration file."),
        scope: str | None = typer.Option(None, "--scope", help="Service scope: user or system."),
    ) -> None:
        raise typer.Exit(_run_action(component, "enable", config_path, scope))

    @sub_app.command("disable", help=f"Do not start the {component.label.lower()} automatically.")
    def disable(
        config_path: str | None = typer.Option(None, "--config", help="Path of the configuration file."),
        scope: str | None = typer.Option(None, "--scope", help="Service scope: user or system."),
    ) -> None:
        raise typer.Exit(_run_action(component, "disable", config_path, scope))

    @sub_app.command("logs", help=f"Show the recent log lines of the {component.label.lower()}.")
    def logs(
        lines: int = typer.Option(50, "--lines", "-n", help="How many lines to show."),
        config_path: str | None = typer.Option(None, "--config", help="Path of the configuration file."),
        scope: str | None = typer.Option(None, "--scope", help="Service scope: user or system."),
    ) -> None:
        raise typer.Exit(_run_action(component, "logs", config_path, scope))


_add_component_commands(dns_app, Component.DNS)
_add_component_commands(proxy_app, Component.PROXY)


# ---------------------------------------------------------------------------
# Top level commands
# ---------------------------------------------------------------------------


@app.command("status", help="Show the state of every component.")
def status_command(
    config_path: str | None = typer.Option(None, "--config", help="Path of the configuration file."),
    scope: str | None = typer.Option(None, "--scope", help="Service scope: user or system."),
) -> None:
    """Print an overview of the installation."""
    config, adapter = _load(config_path)
    if scope:
        adapter.scope = scope
    console.print(_component_table(config, adapter))
    console.print(f"[dim]{adapter.describe()}[/dim]")
    console.print(f"[dim]configuration: {config.path}[/dim]")

    for component in Component:
        if adapter.status(component) is ServiceState.RUNNING_UNREGISTERED:
            console.print(
                f"[yellow]{component.label} runs, but it is not registered.[/yellow]"
                " It will not start again after a reboot or a new login."
            )
            console.print(f"  Run '{_command_name()} install --scope {adapter.scope} --yes' to register it.")


@app.command("check", help="Run the health checks.")
def check_command(
    config_path: str | None = typer.Option(None, "--config", help="Path of the configuration file."),
) -> None:
    """Run every health check and report the result."""
    config, _adapter = _load(config_path)
    checks = run_health_checks(config)

    table = Table(title="Health checks", title_justify="left")
    table.add_column("Check")
    table.add_column("Result")
    table.add_column("Detail")

    failed = False
    for check in checks:
        if check.ok:
            result = "[green]ok[/]"
        elif check.severity == Severity.WARNING:
            result = "[yellow]warning[/]"
            failed = True
        else:
            result = "[red]failed[/]"
            failed = True
        table.add_row(check.name, result, check.detail)

    console.print(table)
    raise typer.Exit(EXIT_ERROR if failed else EXIT_OK)


@app.command("logs", help="Show the recent log lines of every component.")
def logs_command(
    lines: int = typer.Option(50, "--lines", "-n", help="How many lines to show."),
    config_path: str | None = typer.Option(None, "--config", help="Path of the configuration file."),
    scope: str | None = typer.Option(None, "--scope", help="Service scope: user or system."),
) -> None:
    """Show the logs of the DNS resolver and of the proxy."""
    _config, adapter = _load(config_path)
    if scope:
        adapter.scope = scope
    for component in Component:
        result = adapter.logs(component, lines=lines)
        console.print(f"[bold]{component.label}[/bold]")
        if not result.ok:
            console.print(f"[red]{result.message}[/red]")
            if result.detail:
                console.print(f"[dim]{result.detail}[/dim]")
            continue
        output = str(result.data.get("output", "")).strip()
        console.print(output if output else "[dim]no log output[/]", markup=False, highlight=False)
        console.print()


@app.command("install", help="Install the project components.")
def install_command(
    scope: str | None = typer.Option(None, "--scope", help="Service scope: user or system."),
    config_path: str | None = typer.Option(None, "--config", help="Path of the configuration file."),
    start: bool = typer.Option(False, "--start", help="Start the components after the installation."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask for confirmation."),
) -> None:
    """Install the components of this project only."""
    config, adapter = _load(config_path)
    effective_scope = scope or config.service.scope
    adapter.scope = effective_scope

    if not yes and effective_scope == "system":
        console.print(
            "[yellow]A system wide installation changes files outside of your home directory.[/yellow]"
        )
        if not typer.confirm("Continue?"):
            raise typer.Exit(EXIT_ERROR)

    exit_code = EXIT_OK
    installed: list[Component] = []
    failed: list[Component] = []
    for component in Component:
        result = adapter.install(component, scope=effective_scope)
        if result.ok:
            installed.append(component)
        else:
            failed.append(component)
            exit_code = EXIT_ERROR
        _print_result(result)

    if failed and installed:
        # A partial installation must not look like a successful one.
        names = ", ".join(component.label for component in failed)
        error_console.print(
            f"[yellow]The installation is incomplete:[/yellow] {names} could not be installed."
        )
        error_console.print(
            "  The components that were installed work, but they are not registered to start automatically."
        )
        error_console.print(
            f"  Run '{_command_name()} install --scope {effective_scope} --yes' again to retry."
        )

    if start:
        for component in Component:
            _print_result(adapter.start(component))

    raise typer.Exit(exit_code)


@app.command("uninstall", help="Remove the project components.")
def uninstall_command(
    scope: str | None = typer.Option(None, "--scope", help="Service scope: user or system."),
    config_path: str | None = typer.Option(None, "--config", help="Path of the configuration file."),
    purge: bool = typer.Option(False, "--purge", help="Also delete the configuration file."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask for confirmation."),
) -> None:
    """Remove only the resources this project owns."""
    config, adapter = _load(config_path)
    effective_scope = scope or config.service.scope
    adapter.scope = effective_scope

    if not yes and not typer.confirm("Remove the dontneed-dns components?"):
        raise typer.Exit(EXIT_ERROR)

    exit_code = EXIT_OK
    for component in Component:
        result = adapter.uninstall(component, scope=effective_scope)
        exit_code = max(exit_code, 0 if result.ok else EXIT_ERROR)
        _print_result(result)

    if purge and config.path is not None and config.path.exists():
        try:
            config.path.unlink()
            console.print(f"[green]OK[/] {config.path} removed.")
        except OSError as exc:
            error_console.print(f"[red]Failed:[/red] {config.path} could not be removed: {exc}")
            exit_code = EXIT_ERROR

    raise typer.Exit(exit_code)


@app.command("doctor", help="Write a redacted diagnostic report.")
def doctor_command(
    output: Path | None = typer.Option(None, "--output", "-o", help="Write the report to a file."),
    config_path: str | None = typer.Option(None, "--config", help="Path of the configuration file."),
) -> None:
    """Collect the state of the installation in a shareable form."""
    config, adapter = _load(config_path)
    redactor = Redactor()

    report: dict[str, object] = {
        "version": __version__,
        "platform": adapter.diagnostics(),
        "configuration": redactor.value(config.to_dict()),
        "configuration_path": str(config.path),
        "capabilities": [list(row) for row in adapter.capabilities().as_rows()],
        "health": [
            {"check": check.name, "ok": check.ok, "detail": redactor.text(check.detail)}
            for check in run_health_checks(config)
        ],
    }

    text = _render_report(report)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        console.print(f"[green]OK[/] the report was written to {output}")
    else:
        console.print(text, markup=False, highlight=False)


def _render_report(report: dict[str, object]) -> str:
    import json

    redactor = Redactor()
    return redactor.text(json.dumps(report, indent=2, sort_keys=True, default=str))


@app.command("tui", help="Start the terminal user interface.")
def tui_command(
    config_path: str | None = typer.Option(None, "--config", help="Path of the configuration file."),
) -> None:
    """Launch the Textual user interface."""
    from dontneed_dns.tui.app import run_tui

    try:
        run_tui(config_path)
    except Exception as exc:
        error_console.print(f"[red]The interface could not be started:[/red] {exc}")
        raise typer.Exit(EXIT_ERROR) from exc


@app.command("launch", help="Run a command through the local proxy.")
def launch_command(
    command: list[str] = typer.Argument(..., help="The command and its arguments."),
    config_path: str | None = typer.Option(None, "--config", help="Path of the configuration file."),
) -> None:
    """Start ``command`` with the proxy environment variables set.

    Only the launched process is affected; no system wide setting is
    changed.
    """
    if not command:
        error_console.print("[red]No command given.[/red]")
        raise typer.Exit(EXIT_ERROR)

    config, _adapter = _load(config_path)
    proxy_url = f"http://{config.proxy.listen_host}:{config.proxy.listen_port}"

    environment = dict(os.environ)
    for name in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        environment[name] = proxy_url
    # Electron and Chromium honour this flag; it also disables the built in
    # DNS-over-HTTPS so that the local resolver stays in charge.
    environment["ELECTRON_EXTRA_LAUNCH_ARGS"] = environment.get("ELECTRON_EXTRA_LAUNCH_ARGS", "")

    try:
        completed = subprocess.run(command, env=environment, check=False)
    except FileNotFoundError:
        error_console.print(f"[red]The command was not found:[/red] {command[0]}")
        raise typer.Exit(EXIT_ERROR) from None
    raise typer.Exit(completed.returncode)


@app.command("run", hidden=True, help="Run a component in the foreground (used by the services).")
def run_command(
    component: str = typer.Argument(..., help="dns or proxy"),
    config: str | None = typer.Option(None, "--config", help="Path of the configuration file."),
) -> None:
    """Entry point of the service runner."""
    from dontneed_dns.service_runner import main as service_main

    raise typer.Exit(service_main([component, "--config", config] if config else [component]))


@app.command("version", help="Show the version.")
def version_command() -> None:
    """Print the version of the project."""
    console.print(f"dontneed-dns {__version__}")


# ---------------------------------------------------------------------------
# Configuration commands
# ---------------------------------------------------------------------------


@config_app.command("validate", help="Validate the configuration file.")
def config_validate(
    config_path: str | None = typer.Option(None, "--config", help="Path of the configuration file."),
) -> None:
    """Check the configuration file and report every problem."""
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        error_console.print(f"[red]Invalid:[/red] {exc}")
        raise typer.Exit(EXIT_CONFIG) from exc
    console.print(f"[green]The configuration {config.path} is valid.[/green]")


@config_app.command("show", help="Print the effective configuration.")
def config_show(
    config_path: str | None = typer.Option(None, "--config", help="Path of the configuration file."),
) -> None:
    """Print the configuration as it is used."""
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        error_console.print(f"[red]Invalid:[/red] {exc}")
        raise typer.Exit(EXIT_CONFIG) from exc
    console.print(config_to_toml(config), markup=False, highlight=False)


@config_app.command("path", help="Print the path of the configuration file.")
def config_path_command() -> None:
    """Print where the configuration file lives."""
    from dontneed_dns.core.paths import config_path as default_config_path

    console.print(str(default_config_path()))


@config_app.command("init", help="Write a configuration file with the default values.")
def config_init(
    force: bool = typer.Option(False, "--force", help="Overwrite an existing file."),
    config_path: str | None = typer.Option(None, "--config", help="Path of the configuration file."),
) -> None:
    """Create the configuration file."""
    from dontneed_dns.core.paths import config_path as default_config_path

    target = Path(config_path) if config_path else default_config_path()
    if target.exists() and not force:
        error_console.print(f"[red]{target} already exists.[/red] Use --force to overwrite it.")
        raise typer.Exit(EXIT_ERROR)
    save_config(default_config(), target)
    console.print(f"[green]OK[/] {target} written.")


def main() -> None:
    """Entry point of the console script."""
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
