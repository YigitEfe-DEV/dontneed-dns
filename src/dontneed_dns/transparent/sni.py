"""Extraction of the server name from a TLS ClientHello.

The transparent router has to decide what to do with a connection before
any payload is forwarded.  The only piece of information that is available
at that moment is the TLS ClientHello, and its server name extension
carries the host name the client wants to talk to.

The parser is deliberately strict: a message that does not parse yields
``None`` instead of a wrong name.
"""

from __future__ import annotations

__all__ = ["TLS_CLIENT_HELLO", "TLS_HANDSHAKE", "extract_sni", "parse_client_hello"]

TLS_HANDSHAKE = 0x16
TLS_CLIENT_HELLO = 0x01

_EXTENSION_SERVER_NAME = 0x0000


def extract_sni(data: bytes) -> str | None:
    """Return the server name of a TLS ClientHello, or ``None``.

    ``data`` may contain more than the ClientHello; only the beginning is
    inspected.
    """
    return parse_client_hello(data)[0]


def parse_client_hello(data: bytes) -> tuple[str | None, int]:
    """Return the server name and the length of the ClientHello record.

    The length is the size of the TLS record that carries the ClientHello,
    which is what the fragmenter needs to split the stream.
    """
    if len(data) < 5:
        return None, 0
    if data[0] != TLS_HANDSHAKE:
        return None, 0
    record_length = int.from_bytes(data[3:5], "big")
    total = 5 + record_length
    if len(data) < total:
        return None, total
    body = data[5:total]
    if not body or body[0] != TLS_CLIENT_HELLO:
        return None, total

    try:
        offset = 4  # the length of the handshake message
        offset += 2  # the client version
        offset += 32  # the random
        session_length = body[offset]
        offset += 1 + session_length
        cipher_length = int.from_bytes(body[offset : offset + 2], "big")
        offset += 2 + cipher_length
        compression_length = body[offset]
        offset += 1 + compression_length
        if offset + 2 > len(body):
            return None, total
        extensions_length = int.from_bytes(body[offset : offset + 2], "big")
        offset += 2
        end = min(len(body), offset + extensions_length)

        while offset + 4 <= end:
            extension_type = int.from_bytes(body[offset : offset + 2], "big")
            extension_length = int.from_bytes(body[offset + 2 : offset + 4], "big")
            offset += 4
            if offset + extension_length > end:
                return None, total
            if extension_type == _EXTENSION_SERVER_NAME:
                return _parse_server_name(body[offset : offset + extension_length]), total
            offset += extension_length
    except (IndexError, ValueError):
        return None, total
    return None, total


def _parse_server_name(data: bytes) -> str | None:
    """Parse the body of the server name extension."""
    if len(data) < 2:
        return None
    list_length = int.from_bytes(data[0:2], "big")
    offset = 2
    end = min(len(data), 2 + list_length)
    while offset + 3 <= end:
        name_type = data[offset]
        name_length = int.from_bytes(data[offset + 1 : offset + 3], "big")
        offset += 3
        if offset + name_length > end:
            return None
        if name_type == 0x00:
            try:
                return data[offset : offset + name_length].decode("idna")
            except (UnicodeDecodeError, UnicodeError):
                return data[offset : offset + name_length].decode("ascii", "replace")
        offset += name_length
    return None
