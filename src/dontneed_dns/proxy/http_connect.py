"""HTTP CONNECT support for the proxy.

Only the ``CONNECT`` method is implemented; plain HTTP requests are refused
with ``405 Method Not Allowed`` because this proxy exists to tunnel TLS.
"""

from __future__ import annotations

import socket

from dontneed_dns.core.constants import SOCKET_BUFFER_SIZE

__all__ = [
    "MAX_HEADER_SIZE",
    "ConnectTarget",
    "HttpConnectError",
    "read_connect_request",
    "send_error",
    "send_established",
]

MAX_HEADER_SIZE = 16 * 1024


class HttpConnectError(Exception):
    """Raised when the HTTP request is malformed or not a CONNECT request.

    ``status`` holds the HTTP status that has to be sent to the peer before
    the connection is closed.
    """

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class ConnectTarget:
    """The target of a ``CONNECT`` request."""

    __slots__ = ("host", "port")

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"ConnectTarget({self.host!r}, {self.port})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ConnectTarget):
            return NotImplemented
        return self.host == other.host and self.port == other.port


def _read_headers(sock: socket.socket) -> bytes:
    buffer = bytearray()
    while b"\r\n\r\n" not in buffer:
        chunk = sock.recv(min(1024, SOCKET_BUFFER_SIZE))
        if not chunk:
            raise HttpConnectError("connection closed before the request was complete")
        buffer.extend(chunk)
        if len(buffer) > MAX_HEADER_SIZE:
            raise HttpConnectError("request headers are too large")
    return bytes(buffer)


def read_connect_request(sock: socket.socket) -> ConnectTarget:
    """Read a ``CONNECT host:port`` request."""
    raw = _read_headers(sock)
    text = raw.split(b"\r\n", 1)[0].decode("latin-1").strip()
    parts = text.split()
    if len(parts) < 2:
        raise HttpConnectError("malformed request line")
    method = parts[0].upper()
    if method != "CONNECT":
        raise HttpConnectError(f"only CONNECT is supported, got {method}", status=405)

    target = parts[1]
    if target.startswith("["):  # IPv6 literal
        closing = target.find("]")
        if closing < 0:
            raise HttpConnectError("malformed IPv6 authority")
        host = target[1:closing]
        remainder = target[closing + 1 :]
        port = int(remainder[1:]) if remainder.startswith(":") and remainder[1:].isdigit() else 443
    elif ":" in target:
        host, _, port_text = target.rpartition(":")
        if not port_text.isdigit():
            raise HttpConnectError("malformed port")
        port = int(port_text)
    else:
        host = target
        port = 443

    if not host:
        raise HttpConnectError("empty target host")
    if not 0 < port < 65536:
        raise HttpConnectError("port out of range")
    return ConnectTarget(host=host, port=port)


def send_established(sock: socket.socket) -> None:
    """Send the ``200 Connection Established`` response."""
    sock.sendall(b"HTTP/1.1 200 Connection Established\r\nProxy-Agent: dontneed-dns\r\n\r\n")


def send_error(sock: socket.socket, status: int, reason: str) -> None:
    """Send an error response with a minimal HTML body."""
    body = f"<!doctype html><title>{status}</title><h1>{status} {reason}</h1>\n".encode()
    header = (
        f"HTTP/1.1 {status} {reason}\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode("ascii")
    try:
        sock.sendall(header + body)
    except OSError:
        pass
