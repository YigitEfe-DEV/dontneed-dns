"""The UDP relay that reads the packets of the targets from a TUN device.

The packets keep their original addresses when they leave the local
network stack, so the relay knows the address and the port they were
sent to.  It forwards every datagram with a socket that carries the
original source address and port, which keeps the flow of the application
unchanged for the server on the other side.
"""

from __future__ import annotations

import socket
import struct
import threading
import time
from dataclasses import dataclass

from dontneed_dns.core.logging_setup import get_logger
from dontneed_dns.transparent.tun import TunDevice

__all__ = ["TunUdpRelay", "build_udp_packet", "parse_udp_packet"]

_LOGGER = get_logger("transparent.udp")

#: ``IP_TRANSPARENT`` from ``linux/in.h``.
IP_TRANSPARENT = 19

#: The protocol number of UDP.
IPPROTO_UDP = 17

_DEFAULT_IDLE_TIMEOUT = 120.0


@dataclass(frozen=True)
class UdpPacket:
    """One UDP packet of the TUN device."""

    source: str
    source_port: int
    destination: str
    destination_port: int
    payload: bytes


def ip_checksum(header: bytes) -> int:
    """The checksum of an IPv4 header."""
    total = 0
    for index in range(0, len(header), 2):
        total += (header[index] << 8) + header[index + 1]
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def build_udp_packet(
    source: str,
    source_port: int,
    destination: str,
    destination_port: int,
    payload: bytes,
) -> bytes:
    """Build an IPv4 packet that carries UDP.

    The packet is injected into the TUN device, which lets the client see
    the answer with the address of the server, exactly as if the router
    did not exist.
    """
    if ":" in source:
        header = struct.pack(
            "!IHBB16s16s",
            6 << 28,
            8 + len(payload),
            IPPROTO_UDP,
            64,
            socket.inet_pton(socket.AF_INET6, source),
            socket.inet_pton(socket.AF_INET6, destination),
        )
    else:
        total_length = 20 + 8 + len(payload)
        header = struct.pack(
            "!BBHHHBBH4s4s",
            0x45,
            0,
            total_length,
            0,
            0,
            64,
            IPPROTO_UDP,
            0,
            socket.inet_aton(source),
            socket.inet_aton(destination),
        )
        checksum = ip_checksum(header)
        header = header[:10] + struct.pack("!H", checksum) + header[12:]
    # A zero checksum is allowed for UDP over IPv4.
    udp = struct.pack("!HHHH", source_port, destination_port, 8 + len(payload), 0)
    return header + udp + payload


def _is_multicast(address: str) -> bool:
    """``True`` for a multicast or a broadcast address."""
    import ipaddress

    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return parsed.is_multicast


def parse_udp_packet(packet: bytes) -> UdpPacket | None:
    """Parse an IP packet that carries UDP."""
    if len(packet) < 28:
        return None
    version = packet[0] >> 4
    if version == 4:
        header_length = (packet[0] & 0x0F) * 4
        if header_length < 20 or len(packet) < header_length + 8:
            return None
        if packet[9] != IPPROTO_UDP:
            return None
        fragment = struct.unpack_from("!H", packet, 6)[0] & 0x1FFF
        if fragment:
            # A fragment has no header of its own.
            return None
        source = socket.inet_ntop(socket.AF_INET, packet[12:16])
        destination = socket.inet_ntop(socket.AF_INET, packet[16:20])
    elif version == 6:
        header_length = 40
        if len(packet) < header_length + 8:
            return None
        if packet[6] != IPPROTO_UDP:
            return None
        source = socket.inet_ntop(socket.AF_INET6, packet[8:24])
        destination = socket.inet_ntop(socket.AF_INET6, packet[24:40])
    else:
        return None
    offset = header_length
    source_port, destination_port, length = struct.unpack_from("!HHH", packet, offset)
    payload = packet[offset + 8 : offset + length] if length else packet[offset + 8 :]
    return UdpPacket(source, source_port, destination, destination_port, payload)


class TunUdpRelay:
    """Forwards the datagrams that the kernel sends into the TUN device."""

    def __init__(
        self,
        tun: TunDevice,
        *,
        mark: int = 2,
        idle_timeout: float = _DEFAULT_IDLE_TIMEOUT,
        max_flows: int = 512,
    ) -> None:
        self.tun = tun
        self.mark = mark
        self.idle_timeout = idle_timeout
        self.max_flows = max_flows
        self._flows: dict[tuple, _TunFlow] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        """Start reading the device."""
        self._thread = threading.Thread(target=self._read_loop, name="tun-udp", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the relay and release the flows."""
        self._stop.set()
        with self._lock:
            flows = list(self._flows.values())
            self._flows.clear()
        for flow in flows:
            flow.close()

    # -- receiving --------------------------------------------------------

    def _read_loop(self) -> None:
        while not self._stop.is_set():
            try:
                packet = self.tun.read()
            except OSError:
                if self._stop.is_set():
                    break
                continue
            if not packet:
                continue
            parsed = parse_udp_packet(packet)
            if parsed is None:
                continue
            self._handle(parsed)

    # -- forwarding -------------------------------------------------------

    def _handle(self, packet: UdpPacket) -> None:
        """Forward one datagram.

        A datagram that cannot be forwarded is reported; the relay keeps
        running, because the other flows still work.
        """
        try:
            self._forward(packet)
        except Exception as exc:  # noqa: BLE001 - one datagram must not stop the relay
            _LOGGER.info("a datagram could not be forwarded: %s", exc)

    def _forward(self, packet: UdpPacket) -> None:
        """Send one datagram to its destination."""
        if _is_multicast(packet.destination):
            # The router has nothing to do with multicast and broadcast;
            # forwarding them would only feed a loop.
            return
        key = (packet.source, packet.source_port, packet.destination, packet.destination_port)
        with self._lock:
            flow = self._flows.get(key)
            if flow is None:
                if len(self._flows) >= self.max_flows:
                    return
                flow = _TunFlow(self, packet)
                self._flows[key] = flow
        flow.send(packet.payload)


class _TunFlow:
    """One flow: the socket that carries the original source address."""

    def __init__(self, relay: TunUdpRelay, packet: UdpPacket) -> None:
        self.relay = relay
        self.key = (packet.source, packet.source_port, packet.destination, packet.destination_port)
        self._stop = threading.Event()
        self._last = time.monotonic()
        self.source = packet.source
        self.source_port = packet.source_port
        self.destination = packet.destination
        self.destination_port = packet.destination_port
        self._reader: threading.Thread | None = None
        family = socket.AF_INET6 if ":" in packet.source else socket.AF_INET
        self.socket = socket.socket(family, socket.SOCK_DGRAM)
        try:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_MARK, relay.mark)
        except OSError:
            pass
        try:
            # The address of the client is the address of the machine, so
            # the socket uses it as its own.  The port of the client is
            # already in use, so the relay takes a port of its own; the
            # answer is given to the client with the address and the port
            # of the server.
            self.socket.bind((packet.source, 0))
            self.socket.connect((packet.destination, packet.destination_port))
        except OSError as exc:
            _LOGGER.info(
                "the flow %s:%d -> %s:%d could not be opened: %s",
                packet.source,
                packet.source_port,
                packet.destination,
                packet.destination_port,
                exc,
            )
            self.socket.close()
            raise
        self._reader = threading.Thread(target=self._receive, name="tun-udp-flow", daemon=True)
        self._reader.start()
        _LOGGER.info(
            "[transparent] %s:%d -> %s:%d",
            packet.source,
            packet.source_port,
            packet.destination,
            packet.destination_port,
        )

    def _receive(self) -> None:
        """Give the answers of the server back to the client."""
        self.socket.settimeout(1.0)
        while not self._stop.is_set():
            try:
                data = self.socket.recv(65535)
            except (TimeoutError, OSError):
                if time.monotonic() - self._last > self.relay.idle_timeout:
                    break
                continue
            self._last = time.monotonic()
            packet = build_udp_packet(
                self.destination,
                self.destination_port,
                self.source,
                self.source_port,
                data,
            )
            try:
                self.relay.tun.write(packet)
            except OSError as exc:
                _LOGGER.info("the answer could not be given to the client: %s", exc)
                break

    def send(self, payload: bytes) -> None:
        """Forward a datagram."""
        self._last = time.monotonic()
        try:
            self.socket.send(payload)
        except OSError as exc:
            _LOGGER.info("the datagram could not be forwarded: %s", exc)
            self.close()

    def close(self) -> None:
        """Close the flow."""
        try:
            self.socket.close()
        except OSError:
            pass
        with self.relay._lock:
            self.relay._flows.pop(self.key, None)
