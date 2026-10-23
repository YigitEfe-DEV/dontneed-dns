"""Tests of the capability matrix."""

from __future__ import annotations

import pytest

from dontneed_dns.core.capability import (
    LINUX_MATRIX,
    WINDOWS_MATRIX,
    CapabilityLevel,
    Feature,
    matrix_for,
)


def test_every_feature_is_classified_on_both_platforms() -> None:
    for matrix in (LINUX_MATRIX, WINDOWS_MATRIX):
        features = {entry.feature for entry in matrix.entries}
        assert features == set(Feature)


def test_the_matrices_have_no_duplicates() -> None:
    for matrix in (LINUX_MATRIX, WINDOWS_MATRIX):
        features = [entry.feature for entry in matrix.entries]
        assert len(features) == len(set(features))


def test_linux_does_not_claim_windows_features() -> None:
    assert LINUX_MATRIX.level(Feature.SERVICE_SCHEDULED_TASK) is CapabilityLevel.UNSUPPORTED
    assert LINUX_MATRIX.level(Feature.SERVICE_SCM) is CapabilityLevel.UNSUPPORTED


def test_windows_does_not_claim_linux_features() -> None:
    assert WINDOWS_MATRIX.level(Feature.LOGS_JOURNAL) is CapabilityLevel.UNSUPPORTED
    assert WINDOWS_MATRIX.level(Feature.DNS_INTERCEPTION) is CapabilityLevel.UNSUPPORTED


def test_the_shared_features_are_supported_on_both_platforms() -> None:
    shared = (
        Feature.DNS_LOCAL_SERVER,
        Feature.DNS_SPLIT_ROUTING,
        Feature.PROXY_SOCKS5,
        Feature.PROXY_HTTP_CONNECT,
        Feature.TUI,
        Feature.PORTABLE_PACKAGE,
    )
    for feature in shared:
        assert LINUX_MATRIX.is_supported(feature), feature
        assert WINDOWS_MATRIX.is_supported(feature), feature


def test_the_fragmentation_is_experimental_on_both_platforms() -> None:
    for matrix in (LINUX_MATRIX, WINDOWS_MATRIX):
        assert matrix.level(Feature.PROXY_TLS_FRAGMENTATION) is CapabilityLevel.EXPERIMENTAL


def test_matrix_for_returns_the_right_matrix() -> None:
    assert matrix_for("linux") is LINUX_MATRIX
    assert matrix_for("windows") is WINDOWS_MATRIX


def test_matrix_for_rejects_an_unknown_platform() -> None:
    with pytest.raises(KeyError):
        matrix_for("plan9")


def test_every_entry_has_a_label() -> None:
    for matrix in (LINUX_MATRIX, WINDOWS_MATRIX):
        for entry in matrix.entries:
            assert entry.level.label
