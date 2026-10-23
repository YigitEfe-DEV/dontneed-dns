"""Configuration model, loading, validation and serialisation.

The configuration is a TOML document with four sections (``dns``, ``proxy``,
``service`` and ``logging``).  Validation is strict: unknown keys are an
error so that a typo can never silently disable a feature.
"""

from __future__ import annotations

import ipaddress
import tomllib
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from dontneed_dns.core.constants import (
    DEFAULT_DNS_HOST,
    DEFAULT_DNS_HOST_V6,
    DEFAULT_DNS_PORT,
    DEFAULT_FRAGMENT_CHUNK_DELAY_MS,
    DEFAULT_FRAGMENT_CHUNK_SIZE,
    DEFAULT_FRAGMENT_PREAMBLE_DELAY_MS,
    DEFAULT_MAX_CONNECTIONS,
    DEFAULT_MAX_DNS_CONCURRENCY,
    DEFAULT_PROXY_CONNECT_TIMEOUT,
    DEFAULT_PROXY_HOST,
    DEFAULT_PROXY_IDLE_TIMEOUT,
    DEFAULT_PROXY_PORT,
    DEFAULT_SPLIT_DOMAINS,
)
from dontneed_dns.core.doh import DEFAULT_PROVIDERS, select_providers
from dontneed_dns.core.errors import ConfigError
from dontneed_dns.core.paths import config_path

__all__ = [
    "Config",
    "DnsConfig",
    "LoggingConfig",
    "ProxyConfig",
    "ServiceConfig",
    "config_to_toml",
    "default_config",
    "load_config",
    "parse_config",
    "save_config",
    "validate_config",
]

VALID_LEVELS = ("debug", "info", "warning", "error", "critical")
VALID_SCOPES = ("user", "system")
MIN_PORT = 1
MAX_PORT = 65535
MAX_TIMEOUT = 600.0


@dataclass
class DnsConfig:
    """Settings of the DNS-over-HTTPS split resolver."""

    listen_host: str = DEFAULT_DNS_HOST
    listen_port: int = DEFAULT_DNS_PORT
    listen_host_v6: str | None = DEFAULT_DNS_HOST_V6
    split_domains: list[str] = field(default_factory=lambda: list(DEFAULT_SPLIT_DOMAINS))
    route_split: bool = True
    doh_providers: list[str] = field(default_factory=lambda: [p.name for p in DEFAULT_PROVIDERS])
    timeout: float = 3.0
    retries: int = 1
    system_fallback: bool = True
    tcp_enabled: bool = True
    max_concurrency: int = DEFAULT_MAX_DNS_CONCURRENCY


@dataclass
class ProxyConfig:
    """Settings of the SOCKS5 / HTTP CONNECT proxy."""

    listen_host: str = DEFAULT_PROXY_HOST
    listen_port: int = DEFAULT_PROXY_PORT
    enable_socks5: bool = True
    enable_http_connect: bool = True
    resolve_via_doh: bool = True
    allow_system_dns_fallback: bool = True
    fragmentation_enabled: bool = False
    fragment_chunk_size: int = DEFAULT_FRAGMENT_CHUNK_SIZE
    fragment_preamble_delay_ms: int = DEFAULT_FRAGMENT_PREAMBLE_DELAY_MS
    fragment_chunk_delay_ms: int = DEFAULT_FRAGMENT_CHUNK_DELAY_MS
    connect_timeout: float = DEFAULT_PROXY_CONNECT_TIMEOUT
    idle_timeout: float = DEFAULT_PROXY_IDLE_TIMEOUT
    handshake_timeout: float = 15.0
    max_connections: int = DEFAULT_MAX_CONNECTIONS


@dataclass
class ServiceConfig:
    """Settings of the platform service integration."""

    scope: str = "user"
    autostart: bool = False


@dataclass
class LoggingConfig:
    """Settings of the logging subsystem."""

    level: str = "info"
    file: str | None = None
    redact: bool = True


@dataclass
class Config:
    """The complete project configuration."""

    dns: DnsConfig = field(default_factory=DnsConfig)
    proxy: ProxyConfig = field(default_factory=ProxyConfig)
    service: ServiceConfig = field(default_factory=ServiceConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a plain dictionary representation."""
        return {
            "dns": asdict(self.dns),
            "proxy": asdict(self.proxy),
            "service": asdict(self.service),
            "logging": asdict(self.logging),
        }


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_port(value: Any, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigError(f"{field_name} must be an integer port number")
    if not MIN_PORT <= value <= MAX_PORT:
        raise ConfigError(f"{field_name} must be between {MIN_PORT} and {MAX_PORT}")


def _validate_host(value: Any, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{field_name} must be a non-empty host name or IP address")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        for label in value.split("."):
            if not label or len(label) > 63:
                raise ConfigError(f"{field_name} is not a valid host name: {value!r}") from None


def _validate_domain(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{field_name} must be a domain name")
    domain = value.strip().rstrip(".").lower()
    if not domain:
        raise ConfigError(f"{field_name} must not be empty")
    for label in domain.split("."):
        if not label or len(label) > 63:
            raise ConfigError(f"{field_name} contains an invalid label: {value!r}")
        if not all(ch.isalnum() or ch == "-" for ch in label):
            raise ConfigError(f"{field_name} contains an invalid character: {value!r}")
    return domain


def _validate_timeout(value: Any, field_name: str) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ConfigError(f"{field_name} must be a number of seconds")
    if not 0 < float(value) <= MAX_TIMEOUT:
        raise ConfigError(f"{field_name} must be greater than 0 and at most {MAX_TIMEOUT} seconds")


def _validate_bool(value: Any, field_name: str) -> None:
    if not isinstance(value, bool):
        raise ConfigError(f"{field_name} must be true or false")


def _validate_int_range(value: Any, field_name: str, minimum: int, maximum: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigError(f"{field_name} must be an integer")
    if not minimum <= value <= maximum:
        raise ConfigError(f"{field_name} must be between {minimum} and {maximum}")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _build(cls, values: dict[str, Any], section: str) -> Any:
    known = {f.name for f in fields(cls)}
    unknown = set(values) - known
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ConfigError(f"unknown option(s) in [{section}]: {names}")
    return cls(**values)


def parse_config(data: dict[str, Any], *, path: Path | None = None) -> Config:
    """Build a :class:`Config` from a parsed TOML document."""
    if not isinstance(data, dict):
        raise ConfigError("the configuration must be a table")

    known_sections = {"dns", "proxy", "service", "logging"}
    unknown_sections = set(data) - known_sections
    if unknown_sections:
        names = ", ".join(sorted(unknown_sections))
        raise ConfigError(f"unknown configuration section(s): {names}")

    dns = _build(DnsConfig, data.get("dns", {}), "dns")
    proxy = _build(ProxyConfig, data.get("proxy", {}), "proxy")
    service = _build(ServiceConfig, data.get("service", {}), "service")
    logging = _build(LoggingConfig, data.get("logging", {}), "logging")

    config = Config(dns=dns, proxy=proxy, service=service, logging=logging, path=path)
    validate_config(config)
    return config


def validate_config(config: Config) -> None:
    """Validate every field of a configuration, raising :class:`ConfigError`."""
    _validate_host(config.dns.listen_host, "dns.listen_host")
    _validate_port(config.dns.listen_port, "dns.listen_port")
    if config.dns.listen_host_v6 is not None:
        if not str(config.dns.listen_host_v6).strip():
            # An empty value means "do not listen on IPv6".
            config.dns.listen_host_v6 = None
        else:
            _validate_host(config.dns.listen_host_v6, "dns.listen_host_v6")
            try:
                ipaddress.IPv6Address(config.dns.listen_host_v6)
            except ValueError as exc:
                raise ConfigError("dns.listen_host_v6 must be an IPv6 address") from exc
    if not isinstance(config.dns.split_domains, list):
        raise ConfigError("dns.split_domains must be a list of domain names")
    config.dns.split_domains = [
        _validate_domain(domain, "dns.split_domains") for domain in config.dns.split_domains
    ]
    if not isinstance(config.dns.doh_providers, list) or not config.dns.doh_providers:
        raise ConfigError("dns.doh_providers must be a non-empty list")
    for provider in config.dns.doh_providers:
        if not isinstance(provider, str):
            raise ConfigError("dns.doh_providers must contain provider names")
    # Raises ValueError with a helpful message for unknown providers.
    try:
        select_providers(config.dns.doh_providers)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    _validate_timeout(config.dns.timeout, "dns.timeout")
    _validate_int_range(config.dns.retries, "dns.retries", 0, 10)
    _validate_bool(config.dns.route_split, "dns.route_split")
    _validate_bool(config.dns.system_fallback, "dns.system_fallback")
    _validate_bool(config.dns.tcp_enabled, "dns.tcp_enabled")
    _validate_int_range(config.dns.max_concurrency, "dns.max_concurrency", 1, 4096)

    _validate_host(config.proxy.listen_host, "proxy.listen_host")
    _validate_port(config.proxy.listen_port, "proxy.listen_port")
    _validate_bool(config.proxy.enable_socks5, "proxy.enable_socks5")
    _validate_bool(config.proxy.enable_http_connect, "proxy.enable_http_connect")
    if not config.proxy.enable_socks5 and not config.proxy.enable_http_connect:
        raise ConfigError("at least one of proxy.enable_socks5 and proxy.enable_http_connect must be enabled")
    _validate_bool(config.proxy.resolve_via_doh, "proxy.resolve_via_doh")
    _validate_bool(config.proxy.allow_system_dns_fallback, "proxy.allow_system_dns_fallback")
    _validate_bool(config.proxy.fragmentation_enabled, "proxy.fragmentation_enabled")
    _validate_int_range(config.proxy.fragment_chunk_size, "proxy.fragment_chunk_size", 1, 4096)
    _validate_int_range(
        config.proxy.fragment_preamble_delay_ms, "proxy.fragment_preamble_delay_ms", 0, 10_000
    )
    _validate_int_range(config.proxy.fragment_chunk_delay_ms, "proxy.fragment_chunk_delay_ms", 0, 10_000)
    _validate_timeout(config.proxy.connect_timeout, "proxy.connect_timeout")
    _validate_timeout(config.proxy.idle_timeout, "proxy.idle_timeout")
    _validate_timeout(config.proxy.handshake_timeout, "proxy.handshake_timeout")
    _validate_int_range(config.proxy.max_connections, "proxy.max_connections", 1, 65_535)

    if config.service.scope not in VALID_SCOPES:
        raise ConfigError(f"service.scope must be one of: {', '.join(VALID_SCOPES)}")
    _validate_bool(config.service.autostart, "service.autostart")

    if config.logging.level.lower() not in VALID_LEVELS:
        raise ConfigError(f"logging.level must be one of: {', '.join(VALID_LEVELS)}")
    config.logging.level = config.logging.level.lower()
    if config.logging.file is not None and not isinstance(config.logging.file, str):
        raise ConfigError("logging.file must be a path or null")
    _validate_bool(config.logging.redact, "logging.redact")


# ---------------------------------------------------------------------------
# Loading and saving
# ---------------------------------------------------------------------------


def default_config() -> Config:
    """Return a configuration with all defaults applied."""
    return Config()


def load_config(path: str | Path | None = None, *, create_missing: bool = False) -> Config:
    """Load the configuration file.

    When the file does not exist the defaults are returned.  With
    ``create_missing`` the default file is written to disk first.
    """
    target = Path(path) if path is not None else config_path()
    if not target.exists():
        if create_missing:
            config = default_config()
            config.path = target
            save_config(config, target)
            return config
        config = default_config()
        config.path = target
        return config

    try:
        with target.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{target} is not valid TOML: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"{target} could not be read: {exc.strerror}") from exc
    return parse_config(data, path=target)


def config_to_toml(config: Config) -> str:
    """Serialise a configuration to a TOML document."""
    lines: list[str] = ["# dontneed-dns configuration", ""]

    def render(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, str):
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            return f'"{escaped}"'
        if value is None:
            return '""'
        raise TypeError(f"unsupported configuration value: {value!r}")

    for section, values in config.to_dict().items():
        lines.append(f"[{section}]")
        for key, value in values.items():
            if isinstance(value, list):
                rendered = ", ".join(render(item) for item in value)
                lines.append(f"{key} = [{rendered}]")
            else:
                lines.append(f"{key} = {render(value)}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def save_config(config: Config, path: str | Path | None = None) -> Path:
    """Write a configuration to disk."""
    target = Path(path) if path is not None else (config.path or config_path())
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(config_to_toml(config), encoding="utf-8")
    return target


def describe_options() -> Iterable[tuple[str, str]]:
    """Yield ``(option, description)`` pairs for the documentation."""
    for section, values in default_config().to_dict().items():
        for key, value in values.items():
            yield f"{section}.{key}", f"{value!r}"
