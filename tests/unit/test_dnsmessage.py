"""Tests of the DNS wire format handling."""

from __future__ import annotations

import struct

import pytest

from dontneed_dns.core.dnsmessage import (
    build_address_response,
    build_empty_response,
    build_error_response,
    build_query,
    encode_name,
    extract_addresses,
    matches_domain,
    normalize_domain,
    parse_message,
    parse_records,
    read_name,
)
from dontneed_dns.core.errors import DNSProtocolError


def test_parse_simple_query() -> None:
    query = build_query("discord.com", qtype=1)
    parsed = parse_message(query)
    assert parsed.question is not None
    assert parsed.question.name == "discord.com"
    assert parsed.question.qtype == 1
    assert parsed.header.ident == 0x1234
    assert parsed.header.recursion_desired is True
    assert parsed.header.is_response is False


def test_parse_rejects_short_message() -> None:
    with pytest.raises(DNSProtocolError):
        parse_message(b"\x00\x01")


def test_parse_reports_a_missing_question_for_a_truncated_message() -> None:
    query = build_query("discord.com")
    parsed = parse_message(query[:-3])
    assert parsed.question is None
    assert parsed.header.ident == 0x1234


def test_read_name_handles_compression_pointer() -> None:
    # "discord.com" at offset 12, followed by a pointer back to it.
    encoded = encode_name("discord.com")
    message = struct.pack("!HHHHHH", 1, 0, 1, 0, 0, 0) + encoded
    message += struct.pack("!H", 0xC000 | 12) + struct.pack("!HH", 1, 1)
    name, _offset = read_name(message, 12 + len(encoded))
    assert name == "discord.com"


def test_read_name_rejects_pointer_loop() -> None:
    message = struct.pack("!HHHHHH", 1, 0, 1, 0, 0, 0)
    message += struct.pack("!H", 0xC000 | 12)
    with pytest.raises(DNSProtocolError):
        read_name(message, 12)


def test_read_name_rejects_oversized_label() -> None:
    message = struct.pack("!HHHHHH", 1, 0, 1, 0, 0, 0) + bytes([64]) + b"a" * 64
    with pytest.raises(DNSProtocolError):
        read_name(message, 12)


def test_encode_name_roundtrip() -> None:
    encoded = encode_name("cdn.discordapp.com")
    name, offset = read_name(encoded, 0)
    assert name == "cdn.discordapp.com"
    assert offset == len(encoded)


def test_encode_name_rejects_empty_label() -> None:
    with pytest.raises(DNSProtocolError):
        encode_name("bad..name")


def test_matches_domain_is_label_aware() -> None:
    domains = ["discord.com", "dis.gd"]
    assert matches_domain("discord.com", domains) is True
    assert matches_domain("cdn.discord.com", domains) is True
    assert matches_domain("dis.gd", domains) is True
    assert matches_domain("mydiscord.com", domains) is False
    assert matches_domain("discord.com.evil.example", domains) is False
    assert matches_domain("example.org", domains) is False


def test_normalize_domain() -> None:
    assert normalize_domain(" Discord.COM. ") == "discord.com"


def test_build_error_response_echoes_the_question() -> None:
    query = build_query("discord.com")
    response = build_error_response(query, 2)
    parsed = parse_message(response)
    assert parsed.header.is_response is True
    assert parsed.header.rcode == 2
    assert parsed.header.ident == query[0] << 8 | query[1]
    assert parsed.question is not None
    assert parsed.question.name == "discord.com"


def test_build_error_response_on_garbage() -> None:
    response = build_error_response(b"\x00" * 4)
    assert len(response) == 12


def test_build_empty_response_without_question() -> None:
    response = build_empty_response(struct.pack("!HHHHHH", 5, 0, 0, 0, 0, 0))
    assert len(response) == 12


def test_parse_records_extracts_addresses() -> None:
    query = build_query("discord.com")
    response = build_address_response(query, ["203.0.113.7", "2001:db8::1"])
    records = parse_records(response)
    assert len(records) == 2
    assert extract_addresses(response) == ["203.0.113.7", "2001:db8::1"]


def test_build_address_response_ignores_invalid_addresses() -> None:
    query = build_query("discord.com")
    response = build_address_response(query, ["not-an-address"])
    assert extract_addresses(response) == []
