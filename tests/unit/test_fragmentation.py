"""Tests of the TLS fragmentation."""

from __future__ import annotations

from dontneed_dns.proxy.fragmentation import FragmentationSettings, TlsFragmenter


def client_hello_record(payload_size: int = 200) -> bytes:
    """Build a TLS record that carries a ClientHello."""
    payload = b"\x01" + b"\x00" * (payload_size - 1)
    return b"\x16\x03\x03" + len(payload).to_bytes(2, "big") + payload


def enabled_settings(**overrides) -> FragmentationSettings:
    values = {
        "enabled": True,
        "chunk_size": 16,
        "preamble_delay_ms": 50,
        "chunk_delay_ms": 10,
    }
    values.update(overrides)
    return FragmentationSettings(**values)  # type: ignore[arg-type]


def test_a_disabled_fragmenter_passes_the_data_through() -> None:
    fragmenter = TlsFragmenter(FragmentationSettings(enabled=False))
    fragments = fragmenter.feed(b"\x16\x03\x03payload")
    assert len(fragments) == 1
    assert fragments[0].data == b"\x16\x03\x03payload"


def test_a_non_tls_stream_is_passed_through() -> None:
    fragmenter = TlsFragmenter(enabled_settings())
    fragments = fragmenter.feed(b"GET / HTTP/1.1\r\n")
    assert len(fragments) == 1
    assert fragments[0].data == b"GET / HTTP/1.1\r\n"
    assert fragmenter.fragmented is False


def test_a_client_hello_is_split_into_small_pieces() -> None:
    record = client_hello_record(200)
    fragmenter = TlsFragmenter(enabled_settings())
    fragments = fragmenter.feed(record)

    assert fragmenter.fragmented is True
    assert len(fragments) > 3
    # The first piece is the record header.
    assert fragments[0].data == record[:5]
    # The second piece is the preamble.
    assert fragments[1].data == record[5:20]
    # Everything after that is split into chunks of the configured size.
    assert len(fragments[2].data) == 16
    # The pieces together are the original record.
    assert b"".join(fragment.data for fragment in fragments) == record


def test_the_delays_are_applied_to_the_right_pieces() -> None:
    record = client_hello_record(200)
    fragmenter = TlsFragmenter(enabled_settings(preamble_delay_ms=50, chunk_delay_ms=10))
    fragments = fragmenter.feed(record)

    assert fragments[0].delay == 0.0
    assert fragments[1].delay == 0.05
    assert all(fragment.delay == 0.01 for fragment in fragments[2:])


def test_a_record_that_arrives_in_pieces_is_buffered() -> None:
    record = client_hello_record(200)
    fragmenter = TlsFragmenter(enabled_settings())

    fragments = fragmenter.feed(record[:10])
    assert fragments == []

    fragments = fragmenter.feed(record[10:])
    assert fragmenter.fragmented is True
    assert b"".join(fragment.data for fragment in fragments) == record


def test_a_tls_handshake_that_is_not_a_client_hello_is_passed_through() -> None:
    payload = b"\x02" + b"\x00" * 40
    record = b"\x16\x03\x03" + len(payload).to_bytes(2, "big") + payload
    fragmenter = TlsFragmenter(enabled_settings())
    fragments = fragmenter.feed(record)
    assert len(fragments) == 1
    assert fragments[0].data == record
    assert fragmenter.fragmented is False


def test_the_traffic_after_the_first_record_is_not_fragmented() -> None:
    record = client_hello_record(60)
    fragmenter = TlsFragmenter(enabled_settings())
    fragmenter.feed(record)
    fragments = fragmenter.feed(b"plain bytes")
    assert len(fragments) == 1
    assert fragments[0].data == b"plain bytes"


def test_the_fragment_sizes_can_be_configured() -> None:
    record = client_hello_record(100)
    fragmenter = TlsFragmenter(enabled_settings(chunk_size=8, preamble_delay_ms=0, chunk_delay_ms=0))
    fragments = fragmenter.feed(record)
    assert all(len(fragment.data) <= 8 for fragment in fragments[2:])
    assert b"".join(fragment.data for fragment in fragments) == record


def test_flush_returns_the_buffered_bytes() -> None:
    fragmenter = TlsFragmenter(enabled_settings())
    assert fragmenter.feed(b"\x16\x03") == []
    assert fragmenter.flush() == b"\x16\x03"
    assert fragmenter.flush() == b""
