import pytest
from serving.s3_to_volume_sync import (
    normalize_s3_prefix,
    normalize_volume_path,
    relative_object_key,
    destination_path,
    ConfigurationError,
    UnsafeObjectKey,
    _check_common
)

@pytest.mark.parametrize("path", [
    "a/b/../c",
    "a/b\\c",
    "a/b\0c",
    "a//b",
    "",
])
def test_check_common_rejections(path):
    with pytest.raises(Exception):
        _check_common(path, Exception)

@pytest.mark.parametrize("prefix", [
    "some/valid/prefix",
    "prefix",
])
def test_normalize_s3_prefix_happy(prefix):
    assert normalize_s3_prefix(prefix) == prefix

@pytest.mark.parametrize("prefix", [
    "/some/prefix",
    "some/prefix/",
    "some/../prefix",
    "some//prefix",
])
def test_normalize_s3_prefix_rejections(prefix):
    with pytest.raises(ConfigurationError):
        normalize_s3_prefix(prefix)

@pytest.mark.parametrize("path", [
    "/Volumes/cat/sch/vol",
    "/Volumes/cat/sch/vol/dir",
    "/Volumes/cat/sch/vol/dir/subdir",
])
def test_normalize_volume_path_happy(path):
    assert normalize_volume_path(path) == path

@pytest.mark.parametrize("path", [
    "Volumes/cat/sch/vol",
    "/Volumes/cat/sch/vol/",
    "/Volumes/cat/sch",
    "/Volumes/cat/sch/vol/../dir",
    "/Volumes/cat/sch//vol",
])
def test_normalize_volume_path_rejections(path):
    with pytest.raises(ConfigurationError):
        normalize_volume_path(path)

@pytest.mark.parametrize("key, prefix, expected", [
    ("some/valid/prefix/file.txt", "some/valid/prefix", "file.txt"),
    ("some/valid/prefix/dir/file.txt", "some/valid/prefix", "dir/file.txt"),
    ("prefix/file.txt", "prefix", "file.txt"),
])
def test_relative_object_key_happy(key, prefix, expected):
    assert relative_object_key(key, prefix) == expected

@pytest.mark.parametrize("key, prefix", [
    ("some/valid/prefix/dir/", "some/valid/prefix"), # directory marker
    ("/some/valid/prefix/file.txt", "some/valid/prefix"), # starts with /
    ("other/prefix/file.txt", "some/valid/prefix"), # out of prefix
    ("some/valid/prefix_other/file.txt", "some/valid/prefix"), # substring prefix out of bounds
    ("some/valid/prefix", "some/valid/prefix"), # empty rel key
    ("some/valid/prefix/../file.txt", "some/valid/prefix"), # ..
    ("some/valid/prefix//file.txt", "some/valid/prefix"), # empty segment
    ("some/valid/prefix/\0file.txt", "some/valid/prefix"), # null byte
    ("some/valid/prefix/internal\\file.txt", "some/valid/prefix"), # backslash
])
def test_relative_object_key_rejections(key, prefix):
    with pytest.raises(UnsafeObjectKey):
        relative_object_key(key, prefix)

@pytest.mark.parametrize("volume_path, rel_key, expected", [
    ("/Volumes/cat/sch/vol", "file.txt", "/Volumes/cat/sch/vol/file.txt"),
    ("/Volumes/cat/sch/vol/dir", "subdir/file.txt", "/Volumes/cat/sch/vol/dir/subdir/file.txt"),
])
def test_destination_path_happy(volume_path, rel_key, expected):
    assert destination_path(volume_path, rel_key) == expected

@pytest.mark.parametrize("volume_path, rel_key", [
    ("/Volumes/cat/sch/vol", "dir/"), # directory marker
    ("/Volumes/cat/sch/vol", "/file.txt"), # starts with /
    ("/Volumes/cat/sch/vol", "../file.txt"), # ..
    ("/Volumes/cat/sch/vol", "dir//file.txt"), # empty segment
])
def test_destination_path_rejections(volume_path, rel_key):
    with pytest.raises(UnsafeObjectKey):
        destination_path(volume_path, rel_key)
