"""A correct SOCKS5 server implementation (RFC 1928).

The prototype implementation read the greeting and the request with a single
``recv`` call, which truncates the request whenever the client sends it in
more than one segment.  Everything here reads an exact number of bytes.
"""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass

from dontneed_dns.core.constants import SOCKET_BUFFER_SIZE

__all__ = [
    "ATYP_DOMAIN",
    "ATYP_IPV4",
    "ATYP_IPV6",
    "CMD_CONNECT",
    "REPLY_ADDRESS_TYPE_NOT_SUPPORTED",
    "REPLY_COMMAND_NOT_SUPPORTED",
    "REPLY_CONNECTION_REFUSED",
    "REPLY_GENERAL_FAILURE",
    "REPLY_HOST_UNREACHABLE",
    "REPLY_SUCCEEDED",
    "SOCKS5_VERSION",
    "SOCKS_VERSION",
    "Socks5Error",
    "Socks5Request",
    "client_greeting",
    "negotiate",
    "read_request",
    "recv_exact",
    "send_reply",
]

SOCKS_VERSION = 0x05
SOCKS5_VERSION = 0x05
METHOD_NO_AUTHENTICATION = 0x00
METHOD_NO_ACCEPTABLE = 0xFF

ATYP_IPV4 = 0x01
ATYP_DOMAIN = 0x03
ATYP_IPV6 = 0x04

CMD_CONNECT = 0x01
CMD_BIND = 0x02
CMD_UDP_ASSOCIATE = 0x03

REPLY_SUCCEEDED = 0x00
REPLY_GENERAL_FAILURE = 0x01
REPLY_NOT_ALLOWED = 0x02
REPLY_NETWORK_UNREACHABLE = 0x03
REPLY_HOST_UNREACHABLE = 0x04
REPLY_CONNECTION_REFUSED = 0x05
REPLY_TTL_EXPIRED = 0x06
REPLY_COMMAND_NOT_SUPPORTED = 0x07
REPLY_ADDRESS_TYPE_NOT_SUPPORTED = 0x08


class Socks5Error(Exception):
    """Raised when the peer violates the SOCKS5 protocol.

    ``reply_code`` holds the SOCKS5 reply that has to be sent to the peer
    before the connection is closed, if any.
    """

    def __init__(self, message: str, reply_code: int | None = None) -> None:
        super().__init__(message)
        self.reply_code = reply_code


@dataclass(frozen=True)
class Socks5Request:
    """A decoded SOCKS5 CONNECT request."""

    host: str
    port: int
    atyp: int

    @property
    def is_domain(self) -> bool:
        """``True`` when the host was given as a domain name."""
        return self.atyp == ATYP_DOMAIN


def recv_exact(sock: socket.socket, count: int) -> bytes:
    """Read exactly ``count`` bytes or raise :class:`Socks5Error`."""
    if count == 0:
        return b""
    buffer = bytearray()
    while len(buffer) < count:
        chunk = sock.recv(min(count - len(buffer), SOCKET_BUFFER_SIZE))
        if not chunk:
            raise Socks5Error("connection closed while the request was read")
        buffer.extend(chunk)
    return bytes(buffer)


def negotiate(sock: socket.socket) -> None:
    """Perform the server side of the SOCKS5 method negotiation."""
    greeting = recv_exact(sock, 2)
    if greeting[0] != SOCKS5_VERSION:
        raise Socks5Error(f"unsupported SOCKS version {greeting[0]}")
    method_count = greeting[1]
    if method_count == 0:
        raise Socks5Error("no authentication methods offered")
    methods = recv_exact(sock, method_count)
    if METHOD_NO_AUTHENTICATION not in methods:
        sock.sendall(bytes((SOCKS5_VERSION, METHOD_NO_ACCEPTABLE)))
        raise Socks5Error("no acceptable authentication method")
    sock.sendall(bytes((SOCKS5_VERSION, METHOD_NO_AUTHENTICATION)))


def client_greeting(sock: socket.socket) -> None:
    """Perform the client side of the SOCKS5 method negotiation.

    Only the "no authentication required" method is offered.  The helper is
    used by the test-suite and by the ``launch`` helper of the CLI.
    """
    sock.sendall(bytes((SOCKS5_VERSION, 1, METHOD_NO_AUTHENTICATION)))
    reply = recv_exact(sock, 2)
    if reply[0] != SOCKS5_VERSION or reply[1] != METHOD_NO_AUTHENTICATION:
        raise Socks5Error(f"the proxy did not accept the no-authentication method: {reply!r}")


def read_request(sock: socket.socket) -> Socks5Request:
    """Read a SOCKS5 request and return the target."""
    header = recv_exact(sock, 4)
    if header[0] != SOCKS5_VERSION:
        raise Socks5Error(f"unsupported SOCKS version {header[0]}")
    command = header[1]
    if header[2] != 0x00:
        raise Socks5Error("reserved byte must be zero")
    atyp = header[3]

    if atyp == ATYP_IPV4:
        host = socket.inet_ntop(socket.AF_INET, recv_exact(sock, 4))
    elif atyp == ATYP_IPV6:
        host = socket.inet_ntop(socket.AF_INET6, recv_exact(sock, 16))
    elif atyp == ATYP_DOMAIN:
        length = recv_exact(sock, 1)[0]
        if length == 0:
            raise Socks5Error("empty domain name")
        raw = recv_exact(sock, length)
        try:
            host = raw.decode("ascii")
        except UnicodeDecodeError:
            try:
                host = raw.decode("idna")
            except UnicodeError:
                raise Socks5Error("domain name is not valid") from None
    else:
        raise Socks5Error(f"unsupported address type {atyp}", REPLY_ADDRESS_TYPE_NOT_SUPPORTED)

    port = struct.unpack("!H", recv_exact(sock, 2))[0]

    if command != CMD_CONNECT:
        raise Socks5Error(f"only CONNECT is supported, got {command}", REPLY_COMMAND_NOT_SUPPORTED)

    return Socks5Request(host=host, port=port, atyp=atyp)


def send_reply(sock: socket.socket, code: int, bound_address: str = "0.0.0.0", bound_port: int = 0) -> None:
    """Send a SOCKS5 reply."""
    try:
        packed = socket.inet_pton(socket.AF_INET, bound_address)
        atyp = ATYP_IPV4
    except OSError:
        try:
            packed = socket.inet_pton(socket.AF_INET6, bound_address)
            atyp = ATYP_IPV6
        except OSError:
            packed = b"\x00\x00\x00\x00"
            atyp = ATYP_IPV4
    sock.sendall(bytes((SOCKS5_VERSION, code, 0x00, atyp)) + packed + struct.pack("!H", bound_port))
