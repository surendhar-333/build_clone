"""Unit tests for the pure S3-to-Volume synchronization helpers."""

from __future__ import annotations

import argparse

import pytest

from serving import s3_to_volume_sync as sync


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("landing", "landing"),
        ("/landing/", "landing"),
        ("  /landing/internal/  ", "landing/internal"),
        ("landing/internal/network", "landing/internal/network"),
    ],
)
def test_normalize_s3_prefix_happy_path(value: str, expected: str) -> None:
    """S3 prefixes are stripped and normalized without changing their layout."""

    assert sync.normalize_s3_prefix(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        " ",
        "/",
        ".",
        "..",
        "landing/../secret",
        "landing/./internal",
        "landing//internal",
    ],
)
def test_normalize_s3_prefix_rejects_unsafe_values(value: str) -> None:
    """Empty, ambiguous, and traversal-capable prefixes are rejected."""

    with pytest.raises(sync.ConfigurationError):
        sync.normalize_s3_prefix(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "/Volumes/settlement/landing/files",
            "/Volumes/settlement/landing/files",
        ),
        (
            "Volumes/settlement/landing/files",
            "/Volumes/settlement/landing/files",
        ),
        (
            " /Volumes/catalog/schema/volume/internal/ ",
            "/Volumes/catalog/schema/volume/internal",
        ),
        (
            "/Volumes/catalog/schema/volume/",
            "/Volumes/catalog/schema/volume",
        ),
    ],
)
def test_normalize_volume_path_happy_path(
    value: str,
    expected: str,
) -> None:
    """Valid managed Volume paths are returned in canonical form."""

    assert sync.normalize_volume_path(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "/",
        "/dbfs/landing/files",
        "/mnt/landing/files",
        "/Volumes",
        "/Volumes/catalog",
        "/Volumes/catalog/schema",
        "/Volumes//schema/volume",
        "/Volumes/./schema/volume",
        "/Volumes/catalog/../volume",
        "/Volumes/catalog/schema/volume/../../../escape",
    ],
)
def test_normalize_volume_path_rejects_invalid_paths(value: str) -> None:
    """Non-Volume, incomplete, and traversal paths are rejected."""

    with pytest.raises(sync.ConfigurationError):
        sync.normalize_volume_path(value)


@pytest.mark.parametrize(
    ("key", "prefix", "expected"),
    [
        (
            "landing/internal/business_date=2026-08-30/part-1.csv",
            "landing",
            "internal/business_date=2026-08-30/part-1.csv",
        ),
        (
            "landing/network/file.csv",
            "landing",
            "network/file.csv",
        ),
        (
            "company/settlements/landing/internal/file.csv",
            "company/settlements/landing",
            "internal/file.csv",
        ),
    ],
)
def test_relative_object_key_happy_path(
    key: str,
    prefix: str,
    expected: str,
) -> None:
    """S3 keys are mapped relative to the configured landing prefix."""

    assert sync.relative_object_key(key, prefix) == expected


@pytest.mark.parametrize(
    ("key", "prefix"),
    [
        # The object is outside the configured source prefix.
        ("archive/internal/file.csv", "landing"),
        ("landing-other/internal/file.csv", "landing"),
        ("landing", "landing"),
        # Directory-marker objects are not files.
        ("landing/", "landing"),
        ("landing/internal/", "landing"),
        # Empty and traversal path segments are unsafe.
        ("landing//file.csv", "landing"),
        ("landing/./file.csv", "landing"),
        ("landing/../file.csv", "landing"),
        ("landing/internal/../../file.csv", "landing"),
        # Backslashes could be interpreted as separators on some systems.
        ("landing/internal\\file.csv", "landing"),
        # Null bytes are never valid destination path characters.
        ("landing/internal/\x00file.csv", "landing"),
    ],
)
def test_relative_object_key_rejects_unsafe_keys(
    key: str,
    prefix: str,
) -> None:
    """Unmappable S3 keys are rejected before Volume access."""

    with pytest.raises(sync.UnsafeObjectKey):
        sync.relative_object_key(key, prefix)


@pytest.mark.parametrize(
    ("volume_path", "relative_key", "expected"),
    [
        (
            "/Volumes/settlement/landing/files",
            "internal/file.csv",
            "/Volumes/settlement/landing/files/internal/file.csv",
        ),
        (
            "/Volumes/catalog/schema/volume",
            "network/business_date=2026-08-30/part.csv",
            (
                "/Volumes/catalog/schema/volume/"
                "network/business_date=2026-08-30/part.csv"
            ),
        ),
    ],
)
def test_destination_path_happy_path(
    volume_path: str,
    relative_key: str,
    expected: str,
) -> None:
    """Relative object keys are safely appended to the Volume root."""

    assert sync.destination_path(volume_path, relative_key) == expected


@pytest.mark.parametrize(
    "relative_key",
    [
        "../escape.csv",
        "../../escape.csv",
        "../../../schema/escape.csv",
        "../../../../Volumes/other/schema/volume/escape.csv",
    ],
)
def test_destination_path_rejects_path_traversal(
    relative_key: str,
) -> None:
    """A relative key cannot escape the configured Volume destination."""

    with pytest.raises(sync.UnsafeObjectKey):
        sync.destination_path(
            "/Volumes/settlement/landing/files",
            relative_key,
        )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ('"abc123"', "abc123"),
        ("abc123", "abc123"),
        ('""', ""),
        ("", ""),
        (None, ""),
        ('"multipart-etag-2"', "multipart-etag-2"),
    ],
)
def test_strip_etag(
    value: str | None,
    expected: str,
) -> None:
    """S3 ETag quote characters are removed consistently."""

    assert sync.strip_etag(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1", 1),
        ("15", 15),
        ("3600", 3600),
    ],
)
def test_positive_integer_happy_path(value: str, expected: int) -> None:
    """Strictly positive command-line integers are accepted."""

    assert sync.positive_integer(value) == expected


@pytest.mark.parametrize("value", ["0", "-1", "-100"])
def test_positive_integer_rejects_non_positive_values(value: str) -> None:
    """Zero and negative command-line values are rejected."""

    with pytest.raises(argparse.ArgumentTypeError):
        sync.positive_integer(value)


@pytest.mark.parametrize("value", ["abc", "1.5", "", " "])
def test_positive_integer_rejects_non_integer_values(value: str) -> None:
    """Values that cannot be parsed as integers are rejected."""

    with pytest.raises(ValueError):
        sync.positive_integer(value)