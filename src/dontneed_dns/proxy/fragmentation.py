"""TLS ClientHello fragmentation.

Some middleboxes inspect the TLS ClientHello to read the SNI and drop the
connection.  Splitting the first TLS record into small pieces makes it
harder for such an inspector to see the whole ClientHello in one segment.

This is **experimental**.  It changes only the packet boundaries, never the
encrypted payload, and it cannot be verified against a specific middlebox
from this project.  It is therefore opt-in and disabled by default.
"""

from __future__ import annotations

from dataclasses import dataclass

from dontneed_dns.core.constants import (
    DEFAULT_FRAGMENT_CHUNK_DELAY_MS,
    DEFAULT_FRAGMENT_CHUNK_SIZE,
    DEFAULT_FRAGMENT_HEADER_SIZE,
    DEFAULT_FRAGMENT_PREAMBLE_DELAY_MS,
    DEFAULT_FRAGMENT_PREAMBLE_SIZE,
)

__all__ = [
    "TLS_CLIENT_HELLO",
    "TLS_HANDSHAKE",
    "Fragment",
    "FragmentationSettings",
    "TlsFragmenter",
]

TLS_HANDSHAKE = 0x16
TLS_CLIENT_HELLO = 0x01

#: Upper bound for the buffered header.  A stream that sends more than this
#: without completing a TLS record is passed through unchanged.
MAX_BUFFERED = 16 * 1024


@dataclass(frozen=True)
class FragmentationSettings:
    """Tunables of the fragmenter."""

    enabled: bool = False
    chunk_size: int = DEFAULT_FRAGMENT_CHUNK_SIZE
    preamble_delay_ms: int = DEFAULT_FRAGMENT_PREAMBLE_DELAY_MS
    chunk_delay_ms: int = DEFAULT_FRAGMENT_CHUNK_DELAY_MS
    header_size: int = DEFAULT_FRAGMENT_HEADER_SIZE
    preamble_size: int = DEFAULT_FRAGMENT_PREAMBLE_SIZE

    @property
    def preamble_delay(self) -> float:
        """The delay before the preamble, in seconds."""
        return self.preamble_delay_ms / 1000.0

    @property
    def chunk_delay(self) -> float:
        """The delay between two chunks, in seconds."""
        return self.chunk_delay_ms / 1000.0


@dataclass(frozen=True)
class Fragment:
    """One piece of the stream together with the delay before it."""

    data: bytes
    delay: float = 0.0


class TlsFragmenter:
    """Splits the first TLS record of a byte stream into small pieces.

    Usage::

        fragmenter = TlsFragmenter(settings)
        for fragment in fragmenter.feed(chunk):
            time.sleep(fragment.delay)
            sock.sendall(fragment.data)
    """

    def __init__(self, settings: FragmentationSettings) -> None:
        self.settings = settings
        self._buffer = bytearray()
        self._state = "start"  # start | buffering | done
        self._did_fragment = False

    @property
    def finished(self) -> bool:
        """``True`` once the first record was handled."""
        return self._state == "done"

    @property
    def fragmented(self) -> bool:
        """``True`` once a ClientHello was actually split."""
        return self._did_fragment

    def feed(self, data: bytes) -> list[Fragment]:
        """Add ``data`` to the stream and return the pieces to write."""
        if not data:
            return []
        if self._state == "done" or not self.settings.enabled:
            self._state = "done"
            return [Fragment(data)]

        self._buffer.extend(data)
        header_size = self.settings.header_size

        if self._state == "start":
            if self._buffer[0] != TLS_HANDSHAKE:
                # Not TLS at all: pass everything through unchanged.
                pieces = [Fragment(bytes(self._buffer))]
                self._buffer.clear()
                self._state = "done"
                return pieces
            self._state = "buffering"

        if len(self._buffer) < header_size:
            return []

        record_length = int.from_bytes(self._buffer[3:5], "big")
        total = header_size + record_length

        # A ClientHello carries the SNI; other handshake types are left alone.
        if len(self._buffer) < 6 or self._buffer[5] != TLS_CLIENT_HELLO:
            pieces = [Fragment(bytes(self._buffer))]
            self._buffer.clear()
            self._state = "done"
            return pieces

        if len(self._buffer) < total:
            if len(self._buffer) > MAX_BUFFERED:
                pieces = [Fragment(bytes(self._buffer))]
                self._buffer.clear()
                self._state = "done"
            return []

        record = bytes(self._buffer[:total])
        self._buffer.clear()
        self._state = "done"
        self._did_fragment = True
        pieces = self.split_record(record)
        fragments = [Fragment(piece) for piece in pieces]
        if len(fragments) > 1:
            fragments[1] = Fragment(fragments[1].data, self.settings.preamble_delay)
            for index in range(2, len(fragments)):
                fragments[index] = Fragment(fragments[index].data, self.settings.chunk_delay)
        if self._buffer:
            fragments.append(Fragment(bytes(self._buffer)))
            self._buffer.clear()
        return fragments

    def split_record(self, record: bytes) -> list[bytes]:
        """Split one TLS record into the configured pieces."""
        header_size = self.settings.header_size
        preamble_size = self.settings.preamble_size
        chunk_size = max(1, self.settings.chunk_size)

        pieces: list[bytes] = [record[:header_size]]
        if len(record) > header_size:
            pieces.append(record[header_size : header_size + preamble_size])
            remaining = record[header_size + preamble_size :]
            for index in range(0, len(remaining), chunk_size):
                pieces.append(remaining[index : index + chunk_size])
        return pieces

    def flush(self) -> bytes:
        """Return and clear any buffered bytes."""
        if not self._buffer:
            return b""
        data = bytes(self._buffer)
        self._buffer.clear()
        self._state = "done"
        return data
