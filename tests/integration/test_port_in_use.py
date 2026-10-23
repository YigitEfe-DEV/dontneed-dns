"""Tests of the port check that both servers use."""

from __future__ import annotations

import socket

from dontneed_dns.core.system import port_in_use


def test_a_free_port_is_not_in_use() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        # The socket is bound but not listening, so nothing accepts a
        # connection on the port.
        assert port_in_use("127.0.0.1", port) is False


def test_a_listening_port_is_in_use() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        assert port_in_use("127.0.0.1", port) is True


def test_an_unreachable_host_is_not_in_use() -> None:
    assert port_in_use("127.0.0.1", 1) is False
