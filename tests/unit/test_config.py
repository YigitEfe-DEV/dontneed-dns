"""Tests of the configuration handling."""

from __future__ import annotations

from pathlib import Path

import pytest

from dontneed_dns.core.config import (
    config_to_toml,
    default_config,
    load_config,
    parse_config,
    save_config,
)
from dontneed_dns.core.errors import ConfigError


def test_defaults_are_valid() -> None:
    config = default_config()
    assert config.dns.listen_port == 5354
    assert config.proxy.listen_port == 10808
    assert config.proxy.fragmentation_enabled is False


def test_a_round_trip_preserves_every_value(tmp_path: Path) -> None:
    original = default_config()
    original.dns.split_domains = ["discord.com", "dis.gd"]
    original.proxy.fragmentation_enabled = True
    original.service.scope = "system"

    path = tmp_path / "config.toml"
    save_config(original, path)
    loaded = load_config(path)

    assert loaded.dns.split_domains == ["discord.com", "dis.gd"]
    assert loaded.proxy.fragmentation_enabled is True
    assert loaded.service.scope == "system"


def test_the_serialised_document_is_toml() -> None:
    import tomllib

    data = tomllib.loads(config_to_toml(default_config()))
    assert data["dns"]["listen_port"] == 5354
    assert data["proxy"]["listen_port"] == 10808


def test_a_missing_file_yields_the_defaults(tmp_path: Path) -> None:
    config = load_config(tmp_path / "missing.toml")
    assert config.dns.listen_port == 5354


def test_a_broken_file_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("this is not toml = = =\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_config(path)


def test_an_unknown_option_is_rejected() -> None:
    with pytest.raises(ConfigError, match=r"unknown option"):
        parse_config({"dns": {"listen_portt": 5354}})


def test_an_unknown_section_is_rejected() -> None:
    with pytest.raises(ConfigError, match=r"unknown configuration section"):
        parse_config({"dnsz": {}})


def test_a_port_out_of_range_is_rejected() -> None:
    with pytest.raises(ConfigError, match="between 1 and 65535"):
        parse_config({"dns": {"listen_port": 70000}})


def test_a_port_that_is_not_an_integer_is_rejected() -> None:
    with pytest.raises(ConfigError, match="must be an integer port number"):
        parse_config({"dns": {"listen_port": "5354"}})


def test_an_invalid_domain_is_rejected() -> None:
    with pytest.raises(ConfigError, match="invalid character"):
        parse_config({"dns": {"split_domains": ["dis cord.com"]}})


def test_an_unknown_provider_is_rejected() -> None:
    with pytest.raises(ConfigError, match="unknown DNS-over-HTTPS provider"):
        parse_config({"dns": {"doh_providers": ["nope"]}})


def test_an_invalid_scope_is_rejected() -> None:
    with pytest.raises(ConfigError, match=r"service\.scope must be one of"):
        parse_config({"service": {"scope": "root"}})


def test_an_invalid_log_level_is_rejected() -> None:
    with pytest.raises(ConfigError, match=r"logging\.level must be one of"):
        parse_config({"logging": {"level": "verbose"}})


def test_a_negative_timeout_is_rejected() -> None:
    with pytest.raises(ConfigError, match="greater than 0"):
        parse_config({"dns": {"timeout": -1}})


def test_disabling_both_proxy_protocols_is_rejected() -> None:
    with pytest.raises(ConfigError, match="at least one of"):
        parse_config({"proxy": {"enable_socks5": False, "enable_http_connect": False}})


def test_a_boolean_option_must_be_a_boolean() -> None:
    with pytest.raises(ConfigError, match="must be true or false"):
        parse_config({"dns": {"route_split": "yes"}})


def test_domains_are_normalised() -> None:
    config = parse_config({"dns": {"split_domains": ["Discord.COM."]}})
    assert config.dns.split_domains == ["discord.com"]


def test_create_missing_writes_the_file(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "config.toml"
    config = load_config(path, create_missing=True)
    assert path.exists()
    assert config.path == path
