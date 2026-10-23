"""The proxy server.

Accepts SOCKS5 and HTTP CONNECT connections on the loopback interface and
forwards them to the requested target.  Domain names are resolved through
the split resolver, so a Discord domain is looked up over DNS-over-HTTPS and
never through the blocked plain text resolver.
"""

from __future__ import annotations

import ipaddress
import select
import socket
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from dontneed_dns.core.config import ProxyConfig
from dontneed_dns.core.logging_setup import get_logger
from dontneed_dns.core.resolver import SplitResolver
from dontneed_dns.core.system import port_in_use
from dontneed_dns.proxy import http_connect, socks5
from dontneed_dns.proxy.fragmentation import FragmentationSettings, TlsFragmenter

__all__ = ["ProxyServer"]

_LOGGER = get_logger("proxy.server")

_RELAY_BUFFER = 32 * 1024
_SOCKS5_VERSION = 0x05


class ProxyServer:
    """A SOCKS5 / HTTP CONNECT proxy."""

    def __init__(
        self,
        resolver: SplitResolver,
        config: ProxyConfig | None = None,
        *,
        on_event: Callable[[str, str], None] | None = None,
    ) -> None:
        self.resolver = resolver
        self.config = config or ProxyConfig()
        self._on_event = on_event

        self._socket: socket.socket | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._connection_slots = threading.BoundedSemaphore(max(1, self.config.max_connections))
        self._bound: tuple[str, int] | None = None
        self._active_connections = 0
        self._lock = threading.Lock()

    # -- lifecycle --------------------------------------------------------

    @property
    def bound(self) -> tuple[str, int] | None:
        """The address the proxy listens on."""
        return self._bound

    @property
    def active_connections(self) -> int:
        """The number of connections currently being relayed."""
        with self._lock:
            return self._active_connections

    def bind(self) -> None:
        """Open the listening socket."""
        if port_in_use(self.config.listen_host, self.config.listen_port):
            raise OSError(
                f"the port {self.config.listen_port} on {self.config.listen_host} is already in use; "
                "another instance of the proxy is probably running"
            )
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, self.config.max_connections), thread_name_prefix="proxy"
        )
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.config.listen_host, self.config.listen_port))
        sock.listen(128)
        sock.settimeout(0.5)
        self._socket = sock
        self._bound = sock.getsockname()[:2]
        _LOGGER.info("proxy listening on %s:%d", *self._bound)

    def serve_forever(self) -> None:
        """Accept connections until :meth:`shutdown` is called."""
        if self._socket is None:
            self.bind()
        assert self._socket is not None
        while not self._stop_event.is_set():
            try:
                client, address = self._socket.accept()
            except TimeoutError:
                continue
            except OSError:
                if self._stop_event.is_set():
                    break
                continue
            executor = self._executor
            if executor is None:
                client.close()
                break
            try:
                executor.submit(self._handle_client, client, address)
            except RuntimeError:
                client.close()
                break

    def shutdown(self) -> None:
        """Stop the proxy and release the listening socket."""
        self._stop_event.set()
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    def close(self) -> None:
        """Alias of :meth:`shutdown`."""

    def __enter__(self) -> ProxyServer:
        self.bind()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.shutdown()

    # -- connection handling ---------------------------------------------

    def _emit(self, event: str, detail: str) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event(event, detail)
        except Exception:  # noqa: BLE001 - defensive
            pass

    def _handle_client(self, client: socket.socket, address: tuple) -> None:
        if not self._connection_slots.acquire(blocking=False):
            _LOGGER.warning("connection limit reached, refusing %s", address[0])
            try:
                client.close()
            except OSError:
                pass
            return
        try:
            self._serve_client(client, address)
        finally:
            self._connection_slots.release()

    def _serve_client(self, client: socket.socket, address: tuple) -> None:
        destination: socket.socket | None = None
        try:
            client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            client.settimeout(self.config.handshake_timeout)

            protocol = self._detect_protocol(client)
            if protocol == "socks5" and not self.config.enable_socks5:
                _LOGGER.info("SOCKS5 is disabled, refusing the connection")
                return
            if protocol == "http" and not self.config.enable_http_connect:
                _LOGGER.info("HTTP CONNECT is disabled, refusing the connection")
                http_connect.send_error(client, 405, "Method Not Allowed")
                return

            if protocol == "socks5":
                host, port = self._handle_socks5(client)
            else:
                host, port = self._handle_http_connect(client)
        except socks5.Socks5Error as exc:
            _LOGGER.info("handshake failed: %s", exc)
            if exc.reply_code is not None:
                socks5.send_reply(client, exc.reply_code)
            return
        except http_connect.HttpConnectError as exc:
            _LOGGER.info("handshake failed: %s", exc)
            reason = "Method Not Allowed" if exc.status == 405 else "Bad Request"
            http_connect.send_error(client, exc.status, reason)
            return
        except TimeoutError:
            _LOGGER.info("handshake timed out")
            return
        except OSError as exc:
            _LOGGER.info("connection error: %s", exc)
            return

        try:
            destination = self._connect_target(host, port)
        except OSError as exc:
            _LOGGER.info("connection to %s:%d failed: %s", host, port, exc)
            self._reply_failure(client, protocol)
            return

        _LOGGER.info("[%s] %s:%d connected", protocol, host, port)
        self._emit("connect", f"{host}:{port}")

        try:
            if protocol == "socks5":
                socks5.send_reply(client, socks5.REPLY_SUCCEEDED)
            else:
                http_connect.send_established(client)
        except OSError:
            return

        with self._lock:
            self._active_connections += 1
        try:
            self._relay(client, destination, protocol, host, port)
        finally:
            with self._lock:
                self._active_connections -= 1
            for sock in (client, destination):
                try:
                    sock.close()
                except OSError:
                    pass

    @staticmethod
    def _detect_protocol(client: socket.socket) -> str:
        try:
            initial = client.recv(1, socket.MSG_PEEK)
        except OSError:
            return "http"
        if initial and initial[0] == _SOCKS5_VERSION:
            return "socks5"
        return "http"

    def _handle_socks5(self, client: socket.socket) -> tuple[str | None, int | None]:
        socks5.negotiate(client)
        request = socks5.read_request(client)
        return request.host, request.port

    def _handle_http_connect(self, client: socket.socket) -> tuple[str | None, int | None]:
        target = http_connect.read_connect_request(client)
        return target.host, target.port

    def _reply_failure(self, client: socket.socket, protocol: str) -> None:
        try:
            if protocol == "socks5":
                socks5.send_reply(client, socks5.REPLY_HOST_UNREACHABLE)
            else:
                http_connect.send_error(client, 502, "Bad Gateway")
        except OSError:
            pass

    def _connect_target(self, host: str, port: int) -> socket.socket:
        """Connect to the target, resolving the name first when needed."""
        candidates = self._resolve_target(host)
        if not candidates:
            raise OSError(f"{host} could not be resolved")
        last_error: OSError | None = None
        for address in candidates:
            family = socket.AF_INET6 if ":" in address else socket.AF_INET
            sock = socket.socket(family, socket.SOCK_STREAM)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            try:
                sock.settimeout(self.config.connect_timeout)
                sock.connect((address, port))
                sock.settimeout(None)
                return sock
            except OSError as exc:
                last_error = exc
                try:
                    sock.close()
                except OSError:
                    pass
        if last_error is None:
            raise OSError(f"{host} could not be resolved")
        raise last_error

    def _resolve_target(self, host: str) -> list[str]:
        """Return the addresses to try for ``host``."""
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            return [host]

        addresses: list[str] = []
        if self.config.resolve_via_doh:
            addresses = self.resolver.resolve_addresses(host)
        if not addresses and self.config.allow_system_dns_fallback:
            try:
                infos = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
            except socket.gaierror:
                infos = []
            for info in infos:
                address = info[4][0]
                if address not in addresses:
                    addresses.append(address)
        if not addresses:
            if self.config.allow_system_dns_fallback:
                # Let the operating system try the name itself as a last resort.
                return [host]
            return []
        return addresses

    # -- relaying ---------------------------------------------------------

    def _relay(
        self,
        client: socket.socket,
        destination: socket.socket,
        protocol: str,
        host: str,
        port: int,
    ) -> None:
        settings = FragmentationSettings(
            enabled=self.config.fragmentation_enabled,
            chunk_size=self.config.fragment_chunk_size,
            preamble_delay_ms=self.config.fragment_preamble_delay_ms,
            chunk_delay_ms=self.config.fragment_chunk_delay_ms,
        )
        fragmenter = TlsFragmenter(settings)
        sockets = [client, destination]
        deadline = time.monotonic() + self.config.idle_timeout

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _LOGGER.info("[%s] %s:%d closed after the idle timeout", protocol, host, port)
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
                try:
                    data = sock.recv(_RELAY_BUFFER)
                except TimeoutError:
                    continue
                except OSError:
                    return
                if not data:
                    return
                if sock is client and fragmenter.settings.enabled:
                    try:
                        for fragment in fragmenter.feed(data):
                            if fragment.delay:
                                time.sleep(fragment.delay)
                            other.sendall(fragment.data)
                    except OSError:
                        return
                else:
                    try:
                        other.sendall(data)
                    except OSError:
                        return
                # Activity keeps the connection alive.
                deadline = time.monotonic() + self.config.idle_timeout
