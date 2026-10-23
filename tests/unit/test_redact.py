"""Tests of the diagnostic redaction."""

from __future__ import annotations

from dontneed_dns.core.redact import Redactor, redact_text


def test_the_home_directory_is_replaced() -> None:
    redactor = Redactor(home="/home/alice", user="alice")
    assert redactor.text("/home/alice/.config/dontneed-dns") == "~/.config/dontneed-dns"


def test_the_user_name_is_replaced() -> None:
    redactor = Redactor(home="/home/alice", user="alice")
    assert redactor.text("alice") == "<user>"
    assert redactor.text("alicebob") == "alicebob"


def test_the_host_name_is_replaced() -> None:
    redactor = Redactor(home="/home/alice", user="alice")
    redactor._hostname = "desktop-01"
    assert redactor.text("desktop-01") == "<host>"


def test_loopback_addresses_stay_readable() -> None:
    redactor = Redactor(home="/home/alice", user="alice")
    assert redactor.text("127.0.0.1:5354") == "127.0.0.1:5354"


def test_public_addresses_are_masked() -> None:
    redactor = Redactor(home="/home/alice", user="alice")
    assert redactor.text("203.0.113.7") == "203.x.x.x"


def test_tokens_are_removed() -> None:
    redactor = Redactor(home="/home/alice", user="alice")
    assert "hunter2" not in redactor.text("password=hunter2")


def test_mail_addresses_are_removed() -> None:
    redactor = Redactor(home="/home/alice", user="alice")
    assert redactor.text("write to alice@example.org") == "write to <email>"


def test_structures_are_redacted_recursively() -> None:
    redactor = Redactor(home="/home/alice", user="alice")
    result = redactor.value({"path": "/home/alice/x", "list": ["/home/alice/y"], "number": 3})
    assert result == {"path": "~/x", "list": ["~/y"], "number": 3}


def test_redact_text_helper() -> None:
    assert "/home/bob" not in redact_text("/home/bob/file", home="/home/bob")


def test_an_empty_string_is_returned_unchanged() -> None:
    assert Redactor(home="/home/alice").text("") == ""
