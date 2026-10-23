"""The local DNS server.

The server speaks plain DNS over UDP and TCP on the loopback interface.  It
never modifies the system resolver configuration on its own; pointing an
application at it is an explicit, separate operation.
"""

from __future__ import annotations

import socket
import struct
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from dontneed_dns.core.constants import (
    DNS_HEADER_SIZE,
    MAX_TCP_PAYLOAD,
    MAX_UDP_PAYLOAD,
    SOCKET_BUFFER_SIZE,
)
from dontneed_dns.core.dnsmessage import build_error_response, parse_message
from dontneed_dns.core.errors import DNSProtocolError
from dontneed_dns.core.logging_setup import get_logger
from dontneed_dns.core.resolver import SplitResolver
from dontneed_dns.core.system import port_in_use

__all__ = ["DnsServer"]

#: How often a free port is tried when the operating system chooses it.
EPHEMERAL_PORT_ATTEMPTS = 8

_LOGGER = get_logger("dns.server")


class DnsServer:
    """A UDP and TCP DNS server backed by a :class:`SplitResolver`."""

    def __init__(
        self,
        resolver: SplitResolver,
        *,
        host: str = "127.0.0.1",
        port: int = 5354,
        host_v6: str | None = "::1",
        enable_tcp: bool = True,
        max_concurrency: int = 64,
        on_query: Callable[[str, str], None] | None = None,
    ) -> None:
        self.resolver = resolver
        self.host = host
        self.port = port
        # The port that was asked for; 0 means "the operating system picks
        # one".  It is kept apart from ``port`` so that a retry can ask for
        # another ephemeral port.
        self._requested_port = port
        self.host_v6 = host_v6
        self.enable_tcp = enable_tcp
        self.max_concurrency = max(1, max_concurrency)
        self._on_query = on_query

        self._udp_sockets: list[socket.socket] = []
        self._tcp_socket: socket.socket | None = None
        self._threads: list[threading.Thread] = []
        self._executor: ThreadPoolExecutor | None = None
        self._running = threading.Event()
        self._stop_event = threading.Event()
        self._bound_ports: list[tuple[str, int]] = []

    # -- lifecycle --------------------------------------------------------

    @property
    def bound_ports(self) -> list[tuple[str, int]]:
        """The ``(host, port)`` pairs the server actually listens on."""
        return list(self._bound_ports)

    def bind(self) -> None:
        """Open the listening sockets. Raises ``OSError`` when the port is busy."""
        if self._requested_port != 0 and port_in_use(self.host, self._requested_port):
            raise OSError(
                f"the port {self._requested_port} on {self.host} is already in use; "
                "another instance of the resolver is probably running"
            )
        self._executor = ThreadPoolExecutor(max_workers=self.max_concurrency, thread_name_prefix="dns")
        try:
            chosen = self._bind_family(self.host, self._requested_port)
            if self.host_v6:
                try:
                    self._bind_family(self.host_v6, chosen)
                except OSError as exc:
                    _LOGGER.warning("the IPv6 listener is not available on %s: %s", self.host_v6, exc)
        except OSError as exc:
            self.close()
            raise OSError(f"the DNS listener could not be opened on {self.host}:{self.port}: {exc}") from exc
        except Exception:
            self.close()
            raise

    def _bind_family(self, host: str, port: int) -> int:
        """Bind the UDP and the TCP listener of one address family.

        Both protocols have to use the same port: a client that receives a
        truncated answer retries over TCP at the same port.

        The TCP socket is opened first on purpose.  When the operating
        system chooses the port, it then picks one that is valid for TCP;
        Windows reserves parts of the dynamic range for TCP and a reserved
        port cannot be bound at all.  The UDP socket is bound to the port
        that was chosen for TCP.

        Returns the port the family listens on.
        """
        attempts = EPHEMERAL_PORT_ATTEMPTS if port == 0 else 1
        last_error: OSError | None = None
        for attempt in range(attempts):
            opened: list[socket.socket] = []
            try:
                chosen = port
                if self.enable_tcp:
                    tcp_socket, chosen = self._open_tcp(host, port)
                    opened.append(tcp_socket)
                udp_socket, _ = self._open_udp(host, chosen)
                opened.append(udp_socket)
            except OSError as exc:
                last_error = exc
                for sock in opened:
                    try:
                        sock.close()
                    except OSError:
                        pass
                if attempt < attempts - 1:
                    _LOGGER.debug("the port could not be used for both protocols: %s", exc)
                continue

            self._udp_sockets.append(opened[-1])
            if self.enable_tcp:
                self._tcp_socket = opened[0]
            self.port = chosen
            self._bound_ports.append((host, chosen))
            _LOGGER.info("DNS listener ready on %s:%d (UDP and TCP)", host, chosen)
            return chosen
        assert last_error is not None
        raise last_error

    def _close_sockets(self) -> None:
        """Close the sockets that were opened so far."""
        for sock in self._udp_sockets:
            try:
                sock.close()
            except OSError:
                pass
        self._udp_sockets.clear()
        if self._tcp_socket is not None:
            try:
                self._tcp_socket.close()
            except OSError:
                pass
            self._tcp_socket = None
        self._bound_ports.clear()

    def _open_udp(self, host: str, port: int) -> tuple[socket.socket, int]:
        """Open the UDP socket of one address family."""
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if family == socket.AF_INET6:
            try:
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            except OSError:
                pass
        try:
            sock.bind((host, port))
            sock.settimeout(0.5)
        except OSError:
            sock.close()
            raise
        return sock, sock.getsockname()[1]

    def _open_tcp(self, host: str, port: int) -> tuple[socket.socket, int]:
        """Open the TCP socket of one address family."""
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if family == socket.AF_INET6:
            try:
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            except OSError:
                pass
        try:
            sock.bind((host, port))
            sock.listen(64)
            sock.settimeout(0.5)
        except OSError:
            sock.close()
            raise
        return sock, sock.getsockname()[1]

    def serve_forever(self) -> None:
        """Run the server until :meth:`shutdown` is called."""
        if not self._udp_sockets and self._tcp_socket is None:
            self.bind()
        self._running.set()

        for index, sock in enumerate(self._udp_sockets):
            thread = threading.Thread(
                target=self._udp_loop, args=(sock,), name=f"dns-udp-{index}", daemon=True
            )
            thread.start()
            self._threads.append(thread)

        if self._tcp_socket is not None:
            thread = threading.Thread(target=self._tcp_loop, name="dns-tcp", daemon=True)
            thread.start()
            self._threads.append(thread)

        try:
            while not self._stop_event.wait(0.5):
                pass
        finally:
            self._running.clear()

    def shutdown(self) -> None:
        """Stop the server and release every socket."""
        self._stop_event.set()
        for sock in self._udp_sockets:
            try:
                sock.close()
            except OSError:
                pass
        self._udp_sockets.clear()
        if self._tcp_socket is not None:
            try:
                self._tcp_socket.close()
            except OSError:
                pass
            self._tcp_socket = None
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    def close(self) -> None:
        """Alias of :meth:`shutdown` used by context managers."""

    def __enter__(self) -> DnsServer:
        self.bind()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.shutdown()

    # -- request handling -------------------------------------------------

    def _udp_loop(self, sock: socket.socket) -> None:
        while not self._stop_event.is_set():
            try:
                data, address = sock.recvfrom(MAX_UDP_PAYLOAD)
            except TimeoutError:
                continue
            except OSError:
                if self._stop_event.is_set():
                    break
                continue
            self._submit(self._handle_udp, sock, data, address)

    def _tcp_loop(self) -> None:
        assert self._tcp_socket is not None
        while not self._stop_event.is_set():
            try:
                client, _address = self._tcp_socket.accept()
            except TimeoutError:
                continue
            except OSError:
                if self._stop_event.is_set():
                    break
                continue
            self._submit(self._handle_tcp, client)

    def _submit(self, func: Callable[..., None], *args: object) -> None:
        executor = self._executor
        if executor is None:
            return
        try:
            executor.submit(func, *args)
        except RuntimeError:
            # The executor was shut down concurrently.
            return

    def _handle_udp(self, sock: socket.socket, data: bytes, address: tuple) -> None:
        response = self._build_response(data)
        if response is None:
            return
        try:
            sock.sendto(response, address)
        except OSError:
            pass

    def _handle_tcp(self, client: socket.socket) -> None:
        try:
            client.settimeout(10)
            length_bytes = self._recv_exactly(client, 2)
            if length_bytes is None:
                return
            length = struct.unpack("!H", length_bytes)[0]
            if length == 0 or length > MAX_TCP_PAYLOAD:
                return
            data = self._recv_exactly(client, length)
            if data is None:
                return
            response = self._build_response(data)
            if response is None:
                return
            client.sendall(struct.pack("!H", len(response)) + response)
        except OSError:
            pass
        finally:
            try:
                client.close()
            except OSError:
                pass

    @staticmethod
    def _recv_exactly(sock: socket.socket, count: int) -> bytes | None:
        buffer = bytearray()
        while len(buffer) < count:
            chunk = sock.recv(min(count - len(buffer), SOCKET_BUFFER_SIZE))
            if not chunk:
                return None
            buffer.extend(chunk)
        return bytes(buffer)

    def _build_response(self, data: bytes) -> bytes | None:
        """Turn a request into a response. ``None`` means "drop the request"."""
        if len(data) < DNS_HEADER_SIZE:
            if len(data) < 2:
                return None
            # Too short to hold a header, but the identifier can still be
            # echoed so the client is able to match the answer.
            return struct.pack("!HHHHHH", struct.unpack("!H", data[:2])[0], 0x8001, 0, 0, 0, 0)

        try:
            parsed = parse_message(data)
        except DNSProtocolError:
            return build_error_response(data)

        if parsed.header.is_response:
            return None
        if parsed.header.opcode != 0:
            return build_error_response(data, 4)  # NOTIMP
        if parsed.question is None:
            return build_error_response(data)

        question = parsed.question
        if self._on_query is not None:
            try:
                self._on_query(question.name, question.qtype)
            except Exception:  # noqa: BLE001 - defensive
                pass

        result = self.resolver.resolve_wire(data)
        if not result.ok or result.wire is None:
            _LOGGER.warning("no answer for %s (%s)", question.name, result.error or "unknown error")
            return build_error_response(data, 2)  # SERVFAIL

        response = result.wire
        if len(response) >= DNS_HEADER_SIZE:
            # Only the identifier is taken from the request; the flags of the
            # upstream answer carry the response bit and the response code
            # and must not be overwritten.
            response = data[:2] + response[2:]

        limit = parsed.edns_udp_size or MAX_UDP_PAYLOAD
        if len(response) > limit:
            response = _truncate(response, limit)
        return response


def _truncate(response: bytes, limit: int) -> bytes:
    """Return a response marked as truncated so the client retries over TCP."""
    try:
        parsed = parse_message(response)
    except DNSProtocolError:
        return response[:limit]
    question = parsed.question
    header = struct.pack(
        "!HHHHHH", parsed.header.ident, parsed.header.flags | 0x0200, 1 if question else 0, 0, 0, 0
    )
    if question is None:
        return header
    from dontneed_dns.core.dnsmessage import encode_name

    return header + encode_name(question.name) + struct.pack("!HH", question.qtype, question.qclass)
