"""Minimal but strict DNS wire format handling.

The previous prototype detected Discord queries by searching the raw packet
for the byte string ``b"discord"``.  That produced false positives (any answer
containing the word, or a domain such as ``notdiscord.example``) and silently
forwarded malformed packets.

This module parses the header and the question section properly, understands
compression pointers and exposes the query name so routing decisions are made
on the *domain name* rather than on raw bytes.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence
from dataclasses import dataclass

from dontneed_dns.core.constants import (
    DNS_CLASS_IN,
    DNS_HEADER_SIZE,
    DNS_MAX_LABEL_LENGTH,
    DNS_MAX_NAME_LENGTH,
    DNS_TYPE_OPT,
    RCODE_FORMERR,
    RCODE_NOERROR,
)
from dontneed_dns.core.errors import DNSProtocolError

__all__ = [
    "DnsHeader",
    "DnsQuestion",
    "DnsRecord",
    "ParsedMessage",
    "build_address_response",
    "build_empty_response",
    "build_error_response",
    "build_query",
    "encode_name",
    "extract_addresses",
    "matches_domain",
    "normalize_domain",
    "parse_message",
    "parse_records",
    "read_name",
]

_HEADER = struct.Struct("!HHHHHH")


@dataclass(frozen=True)
class DnsHeader:
    """The twelve byte DNS header."""

    ident: int
    flags: int
    qdcount: int
    ancount: int
    nscount: int
    arcount: int

    @property
    def is_response(self) -> bool:
        """``True`` when the QR bit is set."""
        return bool(self.flags & 0x8000)

    @property
    def opcode(self) -> int:
        """The DNS opcode (0 = standard query)."""
        return (self.flags >> 11) & 0x0F

    @property
    def rcode(self) -> int:
        """The response code."""
        return self.flags & 0x0F

    @property
    def recursion_desired(self) -> bool:
        """``True`` when the RD bit is set."""
        return bool(self.flags & 0x0100)

    @property
    def truncated(self) -> bool:
        """``True`` when the TC bit is set."""
        return bool(self.flags & 0x0200)


@dataclass(frozen=True)
class DnsQuestion:
    """A single question section entry."""

    name: str
    qtype: int
    qclass: int


@dataclass(frozen=True)
class ParsedMessage:
    """The result of parsing a DNS message."""

    header: DnsHeader
    question: DnsQuestion | None
    raw: bytes

    @property
    def has_edns(self) -> bool:
        """``True`` when the message carries an EDNS(0) OPT record."""
        return self.header.arcount > 0

    @property
    def edns_udp_size(self) -> int:
        """The advertised EDNS(0) UDP payload size, or 0 when absent."""
        # A full OPT record walk is not needed for our use: we only need to
        # know whether the client is willing to receive larger datagrams.
        return 4096 if self.has_edns else 0


def read_name(data: bytes, offset: int) -> tuple[str, int]:
    """Read a (possibly compressed) domain name.

    Returns the dotted name and the offset just after the name in the
    *original* record.  Raises :class:`DNSProtocolError` for malformed data.
    """
    labels: list[str] = []
    length = 0
    jumped = False
    end_offset = offset
    seen_pointers: set[int] = set()

    while True:
        if offset >= len(data):
            raise DNSProtocolError("name extends past the end of the message")
        marker = data[offset]

        if marker & 0xC0 == 0xC0:
            if offset + 1 >= len(data):
                raise DNSProtocolError("truncated compression pointer")
            pointer = ((marker & 0x3F) << 8) | data[offset + 1]
            if pointer in seen_pointers:
                raise DNSProtocolError("compression pointer loop")
            seen_pointers.add(pointer)
            if not jumped:
                end_offset = offset + 2
                jumped = True
            offset = pointer
            continue

        if marker & 0xC0:
            raise DNSProtocolError("reserved label type")

        if marker == 0:
            offset += 1
            if not jumped:
                end_offset = offset
            break

        if marker > DNS_MAX_LABEL_LENGTH:
            raise DNSProtocolError("label longer than 63 bytes")

        offset += 1
        if offset + marker > len(data):
            raise DNSProtocolError("truncated label")
        try:
            labels.append(data[offset : offset + marker].decode("ascii"))
        except UnicodeDecodeError as exc:  # pragma: no cover - defensive
            raise DNSProtocolError("non ascii label") from exc
        offset += marker
        length += marker + 1
        if length > DNS_MAX_NAME_LENGTH:
            raise DNSProtocolError("name longer than 255 bytes")

    if not labels:
        return ".", end_offset
    return ".".join(labels), end_offset


def encode_name(name: str) -> bytes:
    """Encode a dotted domain name into wire format."""
    if name in ("", "."):
        return b"\x00"
    out = bytearray()
    for label in name.rstrip(".").split("."):
        encoded = label.encode("ascii")
        if not 1 <= len(encoded) <= DNS_MAX_LABEL_LENGTH:
            raise DNSProtocolError(f"invalid label length in {name!r}")
        out.append(len(encoded))
        out.extend(encoded)
    out.append(0)
    return bytes(out)


def parse_message(data: bytes) -> ParsedMessage:
    """Parse the header and the first question of a DNS message.

    Malformed messages raise :class:`DNSProtocolError`; the caller is
    expected to answer with ``FORMERR`` rather than to crash.
    """
    if len(data) < DNS_HEADER_SIZE:
        raise DNSProtocolError("message shorter than the DNS header")

    ident, flags, qdcount, ancount, nscount, arcount = _HEADER.unpack_from(data, 0)
    header = DnsHeader(ident, flags, qdcount, ancount, nscount, arcount)

    question: DnsQuestion | None = None
    if qdcount:
        try:
            name, offset = read_name(data, DNS_HEADER_SIZE)
            if offset + 4 > len(data):
                raise DNSProtocolError("truncated question section")
            qtype, qclass = struct.unpack_from("!HH", data, offset)
        except DNSProtocolError:
            question = None
        else:
            question = DnsQuestion(name, qtype, qclass)

    return ParsedMessage(header=header, question=question, raw=data)


def build_empty_response(request: bytes, rcode: int = RCODE_NOERROR) -> bytes:
    """Build a response that carries the original question and no answers."""
    try:
        parsed = parse_message(request)
    except DNSProtocolError:
        ident = 0
        question_bytes = b""
        qdcount = 0
        opcode = 0
        recursion_desired = False
    else:
        ident = parsed.header.ident
        opcode = parsed.header.opcode
        recursion_desired = parsed.header.recursion_desired
        if parsed.question is None:
            question_bytes = b""
            qdcount = 0
        else:
            question_bytes = encode_name(parsed.question.name) + struct.pack(
                "!HH", parsed.question.qtype, parsed.question.qclass
            )
            qdcount = 1

    flags = 0x8000 | (opcode << 11) | (rcode & 0x0F)
    if recursion_desired:
        flags |= 0x0100
    header = struct.pack("!HHHHHH", ident, flags, qdcount, 0, 0, 0)
    return header + question_bytes


def build_error_response(request: bytes, rcode: int = RCODE_FORMERR) -> bytes:
    """Build an error response (``FORMERR`` by default) for ``request``."""
    return build_empty_response(request, rcode)


def normalize_domain(name: str) -> str:
    """Normalise a domain name for comparison."""
    return name.strip().rstrip(".").lower()


def matches_domain(name: str, domains: object) -> bool:
    """Return ``True`` when ``name`` is one of ``domains`` or a subdomain.

    The comparison is label aware, so ``discord.com`` matches
    ``cdn.discord.com`` but never ``mydiscord.com``.
    """
    candidate = normalize_domain(name)
    if not candidate:
        return False
    for domain in domains:  # type: ignore[union-attr]
        suffix = normalize_domain(str(domain))
        if not suffix:
            continue
        if candidate == suffix or candidate.endswith("." + suffix):
            return True
    return False


def has_opt_record(data: bytes) -> bool:
    """Return ``True`` when the additional section contains an OPT record."""
    try:
        parsed = parse_message(data)
    except DNSProtocolError:
        return False
    if not parsed.has_edns:
        return False
    # Walk the additional section looking for type 41 (OPT).
    offset = DNS_HEADER_SIZE
    try:
        for _ in range(parsed.header.qdcount):
            _, offset = read_name(data, offset)
            offset += 4
        for section, count in (
            ("an", parsed.header.ancount),
            ("ns", parsed.header.nscount),
            ("ar", parsed.header.arcount),
        ):
            del section
            for _ in range(count):
                _, offset = read_name(data, offset)
                rtype, _rclass, _ttl, rdlength = struct.unpack_from("!HHIH", data, offset)
                offset += 10 + rdlength
                if rtype == DNS_TYPE_OPT:
                    return True
    except (DNSProtocolError, struct.error):
        return False
    return False


@dataclass(frozen=True)
class DnsRecord:
    """A resource record from the answer, authority or additional section."""

    name: str
    rtype: int
    rclass: int
    ttl: int
    data: bytes


def parse_records(data: bytes) -> list[DnsRecord]:
    """Parse the answer, authority and additional sections of a message.

    Records with an unreadable layout are skipped instead of aborting the
    whole parse: a truncated upstream answer still yields usable addresses.
    """
    parsed = parse_message(data)
    records: list[DnsRecord] = []
    offset = DNS_HEADER_SIZE
    try:
        for _ in range(parsed.header.qdcount):
            _, offset = read_name(data, offset)
            offset += 4
        for count in (parsed.header.ancount, parsed.header.nscount, parsed.header.arcount):
            for _ in range(count):
                name, offset = read_name(data, offset)
                if offset + 10 > len(data):
                    break
                rtype, rclass, ttl, rdlength = struct.unpack_from("!HHIH", data, offset)
                offset += 10
                if offset + rdlength > len(data):
                    break
                records.append(DnsRecord(name, rtype, rclass, ttl, data[offset : offset + rdlength]))
                offset += rdlength
    except (DNSProtocolError, struct.error):
        return records
    return records


def extract_addresses(data: bytes) -> list[str]:
    """Return the A and AAAA addresses contained in a DNS response."""
    import socket as _socket

    addresses: list[str] = []
    for record in parse_records(data):
        if record.rtype == 1 and len(record.data) == 4:
            addresses.append(_socket.inet_ntop(_socket.AF_INET, record.data))
        elif record.rtype == 28 and len(record.data) == 16:
            addresses.append(_socket.inet_ntop(_socket.AF_INET6, record.data))
    return addresses


def build_address_response(request: bytes, addresses: Sequence[str]) -> bytes:
    """Build a DNS response carrying A/AAAA answers for ``request``.

    Used by the system resolver fallback on platforms where the operating
    system resolver cannot be queried with a raw DNS message.
    """
    import socket as _socket

    parsed = parse_message(request)
    question = parsed.question
    if question is None:
        return build_empty_response(request)

    flags = 0x8180  # QR + RD + RA, NOERROR
    header = struct.pack("!HHHHHH", parsed.header.ident, flags, 1, len(addresses), 0, 0)
    question_bytes = encode_name(question.name) + struct.pack("!HH", question.qtype, question.qclass)

    answers = bytearray()
    for address in addresses:
        try:
            packed = _socket.inet_pton(_socket.AF_INET, address)
            rtype = 1
        except OSError:
            try:
                packed = _socket.inet_pton(_socket.AF_INET6, address)
                rtype = 28
            except OSError:
                continue
        answers.extend(encode_name(question.name))
        answers.extend(struct.pack("!HHIH", rtype, DNS_CLASS_IN, 300, len(packed)))
        answers.extend(packed)

    return header + question_bytes + bytes(answers)


def build_query(name: str, qtype: int = 1, qclass: int = DNS_CLASS_IN, ident: int = 0x1234) -> bytes:
    """Build a standard recursive DNS query. Used by the test-suite."""
    header = struct.pack("!HHHHHH", ident, 0x0100, 1, 0, 0, 0)
    return header + encode_name(name) + struct.pack("!HH", qtype, qclass)
