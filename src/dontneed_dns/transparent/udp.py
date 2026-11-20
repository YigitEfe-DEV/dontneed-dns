"""The transparent UDP relay.

The kernel redirects the datagrams of a flow to this relay and keeps the
original destination.  The relay forwards every datagram to that
destination and sends the answer back to the client.

The answer is sent with the address of the original destination as its
source, so that the client accepts it: the socket of the client is
connected to the address it wanted to reach.
"""

from __future__ import annotations

import socket
import struct
import threading
import time
from collections.abc import Callable

from dontneed_dns.core.logging_setup import get_logger

__all__ = [
    "IP_ORIGDSTADDR",
    "IP_RECVORIGDSTADDR",
    "IP_TRANSPARENT",
    "TransparentUdpRelay",
]

_LOGGER = get_logger("transparent.udp")


def _v6_host(host: str) -> str:
    """The address to bind for the IPv6 socket."""
    if ":" in host:
        return host
    return "::"


#: ``IP_RECVORIGDSTADDR`` from ``linux/in.h``.
IP_RECVORIGDSTADDR = 20

#: ``IP_ORIGDSTADDR`` from ``linux/in.h``.
IP_ORIGDSTADDR = 20

#: ``IP_TRANSPARENT`` from ``linux/in.h``.
IP_TRANSPARENT = 19

_RECV_BUFFER = 65535
_DEFAULT_IDLE_TIMEOUT = 120.0


class TransparentUdpRelay:
    """Forwards the datagrams of redirected flows."""

    def __init__(
        self,
        port: int = 15002,
        host: str = "0.0.0.0",
        mark: int = 1,
        idle_timeout: float = _DEFAULT_IDLE_TIMEOUT,
        max_flows: int = 512,
        on_event: Callable[[str, str], None] | None = None,
    ) -> None:
        self.port = port
        self.host = host
        self.mark = mark
        self.idle_timeout = idle_timeout
        self.max_flows = max_flows
        self.on_event = on_event

        self._sockets: list[socket.socket] = []
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._flows: dict[tuple, _Flow] = {}
        self._lock = threading.Lock()

    # -- lifecycle --------------------------------------------------------

    def bind(self) -> None:
        """Open the receiving sockets of both address families."""
        for family, host in ((socket.AF_INET, self.host), (socket.AF_INET6, _v6_host(self.host))):
            try:
                sock = socket.socket(family, socket.SOCK_DGRAM)
            except OSError:
                continue
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if family == socket.AF_INET6:
                try:
                    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                except OSError:
                    pass
            try:
                sock.setsockopt(socket.IPPROTO_IP, IP_RECVORIGDSTADDR, 1)
            except OSError as exc:
                _LOGGER.warning("the original destination cannot be received: %s", exc)
            sock.bind((host, self.port))
            self._sockets.append(sock)
        if not self._sockets:
            raise OSError(f"the transparent relay could not listen on {self.host}:{self.port}")

    def serve_forever(self) -> None:
        """Receive datagrams until :meth:`shutdown` is called."""
        if not self._sockets:
            self.bind()
        for index, sock in enumerate(self._sockets):
            thread = threading.Thread(
                target=self._receive_loop, args=(sock,), name=f"udp-tproxy-{index}", daemon=True
            )
            thread.start()
            self._threads.append(thread)
        while not self._stop.wait(0.5):
            pass

    def shutdown(self) -> None:
        """Stop the relay and release the sockets."""
        self._stop.set()
        for sock in self._sockets:
            try:
                sock.close()
            except OSError:
                pass
        self._sockets.clear()
        with self._lock:
            flows = list(self._flows.values())
            self._flows.clear()
        for flow in flows:
            flow.close()

    def close(self) -> None:
        """Alias of :meth:`shutdown`."""

    # -- helpers ----------------------------------------------------------

    def _emit(self, event: str, detail: str) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event(event, detail)
        except Exception:  # noqa: BLE001 - the relay must not crash
            pass

    # -- receiving --------------------------------------------------------

    def _receive_loop(self, sock: socket.socket) -> None:
        while not self._stop.is_set():
            try:
                data, ancillary, _, sender = sock.recvmsg(_RECV_BUFFER, socket.CMSG_SPACE(64))
            except (TimeoutError, OSError):
                if self._stop.is_set():
                    break
                continue
            destination = self._original_destination(ancillary)
            if destination is None:
                continue
            self._handle(sender, destination, data)

    @staticmethod
    def _original_destination(ancillary: list) -> tuple[str, int] | None:
        """Read the original destination from the ancillary data."""
        for level, kind, payload in ancillary:
            if level == socket.IPPROTO_IP and kind == IP_ORIGDSTADDR:
                port = struct.unpack("!H", payload[2:4])[0]
                host = socket.inet_ntoa(payload[4:8])
                return host, port
            if level == socket.IPPROTO_IPV6 and kind == IP_ORIGDSTADDR:
                port = struct.unpack("!H", payload[2:4])[0]
                host = socket.inet_ntop(socket.AF_INET6, payload[8:24])
                return host, port
        return None

    # -- forwarding -------------------------------------------------------

    def _handle(self, sender: tuple, destination: tuple[str, int], data: bytes) -> None:
        """Forward one datagram and make sure the flow exists."""
        key = (sender, destination)
        with self._lock:
            flow = self._flows.get(key)
            if flow is None:
                if len(self._flows) >= self.max_flows:
                    return
                flow = _Flow(self, sender, destination, self.mark, self.idle_timeout)
                self._flows[key] = flow
        flow.send(data)

    def _flow_finished(self, key: tuple) -> None:
        with self._lock:
            self._flows.pop(key, None)

    def _emit_event(self, event: str, detail: str) -> None:
        self._emit(event, detail)


class _Flow:
    """One UDP flow: the client, the destination and the return path."""

    def __init__(
        self,
        relay: TransparentUdpRelay,
        client: tuple,
        destination: tuple[str, int],
        mark: int,
        idle_timeout: float,
    ) -> None:
        self.relay = relay
        self.client = client
        self.destination = destination
        self.idle_timeout = idle_timeout
        self.key = (client, destination)
        self._stop = threading.Event()
        self._closed = False
        self._last = time.monotonic()

        host, port = destination
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        self.outbound = socket.socket(family, socket.SOCK_DGRAM)
        try:
            self.outbound.setsockopt(socket.SOL_SOCKET, socket.SO_MARK, mark)
        except OSError:
            pass
        self.outbound.connect((host, port))
        self._thread = threading.Thread(target=self._receive, name="udp-tproxy-flow", daemon=True)
        self._thread.start()
        _LOGGER.info("[transparent] %s -> %s:%d", client[0], host, port)
        relay._emit_event("connect", f"{host}:{port}")

    def send(self, data: bytes) -> None:
        """Forward a datagram to the destination."""
        self._last = time.monotonic()
        try:
            self.outbound.send(data)
        except OSError as exc:
            _LOGGER.info("the datagram could not be forwarded: %s", exc)
            self.close()

    def _receive(self) -> None:
        """Forward the answers back to the client."""
        host, port = self.destination
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        sender = None
        try:
            sender = socket.socket(family, socket.SOCK_DGRAM)
            sender.setsockopt(socket.SOL_SOCKET, socket.SO_MARK, self.relay.mark)
            sender.setsockopt(socket.IPPROTO_IP, IP_TRANSPARENT, 1)
            sender.bind((host, port))
        except OSError as exc:
            _LOGGER.info("the answer could not be sent with the original source: %s", exc)
            if sender is not None:
                sender.close()
            self.close()
            return
        while not self._stop.is_set():
            try:
                self.outbound.settimeout(1.0)
                data = self.outbound.recv(_RECV_BUFFER)
            except (TimeoutError, OSError):
                if time.monotonic() - self._last > self.idle_timeout:
                    break
                continue
            self._last = time.monotonic()
            try:
                sender.sendto(data, self.client)
            except OSError as exc:
                _LOGGER.info("the answer could not be forwarded: %s", exc)
                break
        sender.close()
        self.close()

    def close(self) -> None:
        """Close the flow."""
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        try:
            self.outbound.close()
        except OSError:
            pass
        self.relay._flow_finished(self.key)
