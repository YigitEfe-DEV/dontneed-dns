"""The transparent TCP proxy.

The kernel redirects a connection to this proxy and keeps the original
destination.  The proxy reads it, decides what to do with the connection
and forwards the bytes.

The decision is taken from the TLS ClientHello: when the server name
belongs to one of the configured domains, the ClientHello is split into
small pieces, which makes it harder for an inspector to read the server
name.  Every other connection is forwarded unchanged.
"""

from __future__ import annotations

import select
import socket
import struct
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from dontneed_dns.core.logging_setup import get_logger
from dontneed_dns.proxy.fragmentation import FragmentationSettings, TlsFragmenter
from dontneed_dns.transparent.sni import parse_client_hello

__all__ = [
    "SOL_IPV6",
    "SO_ORIGINAL_DST",
    "TransparentTcpServer",
    "original_destination",
]

_LOGGER = get_logger("transparent.tcp")


def _v6_host(host: str) -> str:
    """The address to bind for the IPv6 socket."""
    if ":" in host:
        return host
    return "::"


#: ``SO_ORIGINAL_DST`` from ``linux/netfilter_ipv4.h``.
SO_ORIGINAL_DST = 80

#: ``IP6T_SO_ORIGINAL_DST`` from ``linux/netfilter_ipv6.h``.
SOL_IPV6 = 41

_RELAY_BUFFER = 32 * 1024
_DEFAULT_IDLE_TIMEOUT = 300.0
_DEFAULT_CONNECT_TIMEOUT = 10.0
_DEFAULT_HANDSHAKE_TIMEOUT = 15.0


def original_destination(sock: socket.socket) -> tuple[str, int]:
    """Return the address the peer wanted to reach.

    The kernel keeps the original destination of a redirected connection;
    it is read with ``SO_ORIGINAL_DST``.
    """
    family = _socket_family(sock)
    if family == socket.AF_INET6:
        data = sock.getsockopt(SOL_IPV6, SO_ORIGINAL_DST, 28)
        port = struct.unpack("!H", data[2:4])[0]
        host = socket.inet_ntop(socket.AF_INET6, data[8:24])
        return host, port
    data = sock.getsockopt(socket.SOL_IP, SO_ORIGINAL_DST, 16)
    port = struct.unpack("!H", data[2:4])[0]
    host = socket.inet_ntoa(data[4:8])
    return host, port


def _socket_family(sock: socket.socket) -> int:
    """Return the address family of a socket."""
    try:
        return sock.getsockopt(socket.SOL_SOCKET, socket.SO_DOMAIN)
    except (OSError, AttributeError):
        return socket.AF_INET


@dataclass
class TransparentTcpServer:
    """Accepts redirected connections and forwards them."""

    port: int = 15001
    host: str = "0.0.0.0"
    mark: int = 1
    fragment: bool = True
    fragment_chunk_size: int = 16
    fragment_preamble_delay_ms: int = 50
    fragment_chunk_delay_ms: int = 10
    sni_domains: tuple[str, ...] = ()
    connect_timeout: float = _DEFAULT_CONNECT_TIMEOUT
    handshake_timeout: float = _DEFAULT_HANDSHAKE_TIMEOUT
    idle_timeout: float = _DEFAULT_IDLE_TIMEOUT
    max_connections: int = 256
    on_event: Callable[[str, str], None] | None = None

    def __post_init__(self) -> None:
        self._sockets: list[socket.socket] = []
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._active = 0
        self._lock = threading.Lock()
        self._domains = tuple(domain.lower().lstrip(".") for domain in self.sni_domains)

    # -- lifecycle --------------------------------------------------------

    def bind(self) -> None:
        """Open the listening sockets of both address families."""
        for family, host in ((socket.AF_INET, self.host), (socket.AF_INET6, _v6_host(self.host))):
            try:
                sock = socket.socket(family, socket.SOCK_STREAM)
            except OSError:
                continue
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if family == socket.AF_INET6:
                try:
                    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                except OSError:
                    pass
            sock.bind((host, self.port))
            sock.listen(128)
            sock.settimeout(0.5)
            self._sockets.append(sock)
        if not self._sockets:
            raise OSError(f"the transparent proxy could not listen on {self.host}:{self.port}")

    def serve_forever(self) -> None:
        """Accept connections until :meth:`shutdown` is called."""
        if not self._sockets:
            self.bind()
        for index, sock in enumerate(self._sockets):
            thread = threading.Thread(
                target=self._accept_loop, args=(sock,), name=f"tcp-tproxy-{index}", daemon=True
            )
            thread.start()
            self._threads.append(thread)
        while not self._stop.wait(0.5):
            pass

    def shutdown(self) -> None:
        """Stop the proxy and release the sockets."""
        self._stop.set()
        for sock in self._sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        self._sockets.clear()

    def close(self) -> None:
        """Alias of :meth:`shutdown`."""

    # -- helpers ----------------------------------------------------------

    def _emit(self, event: str, detail: str) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event(event, detail)
        except Exception:  # noqa: BLE001 - the proxy must not crash
            pass

    def matches(self, server_name: str | None) -> bool:
        """``True`` when the server name belongs to the configured domains."""
        if not server_name or not self._domains:
            return False
        candidate = server_name.lower().rstrip(".")
        return any(candidate == domain or candidate.endswith("." + domain) for domain in self._domains)

    def _fragmentation_settings(self) -> FragmentationSettings:
        return FragmentationSettings(
            enabled=self.fragment,
            chunk_size=self.fragment_chunk_size,
            preamble_delay_ms=self.fragment_preamble_delay_ms,
            chunk_delay_ms=self.fragment_chunk_delay_ms,
        )

    def _connect(self, host: str, port: int) -> socket.socket:
        """Open the outbound connection to the original destination."""
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_MARK, self.mark)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(self.connect_timeout)
        try:
            sock.connect((host, port))
        except OSError:
            sock.close()
            raise
        sock.settimeout(None)
        return sock

    # -- request handling -------------------------------------------------

    def _accept_loop(self, listener: socket.socket) -> None:
        while not self._stop.is_set():
            try:
                client, address = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                if self._stop.is_set():
                    break
                continue
            thread = threading.Thread(
                target=self._handle, args=(client, address), name="tcp-tproxy", daemon=True
            )
            thread.start()
            self._threads.append(thread)

    def _handle(self, client: socket.socket, address: tuple) -> None:
        with self._lock:
            self._active += 1
        try:
            self._serve(client, address)
        except Exception as exc:  # noqa: BLE001 - one connection must not kill the proxy
            _LOGGER.info("transparent connection failed: %s", exc)
        finally:
            with self._lock:
                self._active -= 1
            try:
                client.close()
            except OSError:
                pass

    def _serve(self, client: socket.socket, address: tuple) -> None:
        try:
            host, port = original_destination(client)
        except OSError as exc:
            _LOGGER.info("the original destination could not be read: %s", exc)
            return
        if not host or host in ("0.0.0.0", "::"):
            _LOGGER.info("the original destination is unknown, the connection is closed")
            return

        client.settimeout(self.handshake_timeout)
        try:
            head = client.recv(_RELAY_BUFFER)
        except (TimeoutError, OSError):
            return
        if not head:
            return

        server_name, _record_length = parse_client_hello(head)
        fragmented = self.fragment and self.matches(server_name)

        destination = None
        try:
            destination = self._connect(host, port)
        except OSError as exc:
            _LOGGER.info("the connection to %s:%d failed: %s", host, port, exc)
            return

        _LOGGER.info(
            "[transparent] %s:%d -> %s:%d server name %s%s",
            address[0],
            address[1],
            host,
            port,
            server_name or "-",
            " (fragmented)" if fragmented else "",
        )
        self._emit("connect", f"{host}:{port}")

        fragmenter = TlsFragmenter(self._fragmentation_settings())
        client.settimeout(None)
        try:
            self._relay(client, destination, head, fragmenter, fragmented)
        finally:
            try:
                destination.close()
            except OSError:
                pass

    def _forward(
        self,
        from_client: bool,
        other: socket.socket,
        data: bytes,
        fragmenter: TlsFragmenter,
        fragmented: bool,
    ) -> bool:
        """Send one piece of data to the other side.

        Returns ``False`` when the connection is broken.
        """
        if from_client and fragmented:
            try:
                for fragment in fragmenter.feed(data):
                    if fragment.delay:
                        time.sleep(fragment.delay)
                    other.sendall(fragment.data)
            except OSError:
                return False
            return True
        try:
            other.sendall(data)
        except OSError:
            return False
        return True

    def _relay(
        self,
        client: socket.socket,
        destination: socket.socket,
        head: bytes,
        fragmenter: TlsFragmenter,
        fragmented: bool,
    ) -> None:
        """Forward the bytes in both directions."""
        deadline = time.monotonic() + self.idle_timeout
        pending: bytes | None = head
        if pending:
            # The bytes that were read before the connection was set up
            # are sent first; they must not wait for the next read.
            if not self._forward(True, destination, pending, fragmenter, fragmented):
                return
            pending = None
        sockets = [client, destination]
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            try:
                readable, _, errored = select.select(sockets, [], sockets, min(remaining, 5.0))
            except (OSError, ValueError):
                return
            if errored:
                return
            if not readable:
                continue
            for sock in readable:
                other = destination if sock is client else client
                chunk: bytes
                if sock is client and pending is not None:
                    chunk = pending
                    pending = None
                else:
                    try:
                        chunk = sock.recv(_RELAY_BUFFER)
                    except (TimeoutError, OSError):
                        return
                    if not chunk:
                        return
                if not self._forward(sock is client, other, chunk, fragmenter, fragmented):
                    return
                deadline = time.monotonic() + self.idle_timeout


def transparent_sni_domains(domains: Iterable[str]) -> tuple[str, ...]:
    """Normalise a list of domains for the matcher."""
    return tuple(domain.strip().lower().lstrip(".") for domain in domains if domain.strip())
