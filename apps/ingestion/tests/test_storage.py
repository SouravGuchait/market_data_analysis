"""Storage adapter contract and local filesystem behaviour."""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from apps.ingestion.storage import (
    LocalStorageAdapter,
    StoragePathError,
    StorageUnavailableError,
    UnsupportedStorageBackendError,
    get_storage_adapter,
)


def test_list_objects_matches_pattern_and_supported_formats(tmp_path: Path) -> None:
    root = tmp_path / "root"
    inbox = root / "inbox"
    nested = inbox / "nested"
    nested.mkdir(parents=True)
    (inbox / "a.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (nested / "b.json").write_text('{"a": 1}', encoding="utf-8")
    (inbox / "ignored.txt").write_text("nope", encoding="utf-8")

    adapter = LocalStorageAdapter(root, "inbox")
    objects = adapter.list_objects(pattern="**/*", formats=["csv", "json"])

    assert [obj.key for obj in objects] == ["a.csv", "nested/b.json"]
    assert objects[0].backend == "local"


def test_stat_returns_timezone_aware_metadata(tmp_path: Path) -> None:
    root = tmp_path / "root"
    inbox = root / "inbox"
    inbox.mkdir(parents=True)
    path = inbox / "ticks.csv"
    path.write_text("a,b\n", encoding="utf-8")
    expected_mtime = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    os.utime(path, (expected_mtime.timestamp(), expected_mtime.timestamp()))

    adapter = LocalStorageAdapter(root, "inbox")
    obj = adapter.stat("ticks.csv")

    assert obj.name == "ticks.csv"
    assert obj.size_bytes == 4
    assert obj.modified_at.tzinfo is not None
    assert int(obj.modified_at.timestamp()) == int(expected_mtime.timestamp())


def test_content_hash_and_reads_match_file_bytes(tmp_path: Path) -> None:
    root = tmp_path / "root"
    inbox = root / "inbox"
    inbox.mkdir(parents=True)
    payload = b"symbol,price\nACME,10.50\n"
    (inbox / "ticks.csv").write_bytes(payload)

    adapter = LocalStorageAdapter(root, "inbox")

    assert adapter.content_hash("ticks.csv") == hashlib.sha256(payload).hexdigest()
    assert adapter.read_bytes("ticks.csv") == payload
    with adapter.open("ticks.csv") as stream:
        assert stream.read() == payload


def test_missing_rule_location_is_not_treated_as_empty(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    adapter = LocalStorageAdapter(root, "missing")

    with pytest.raises(StorageUnavailableError):
        adapter.list_objects(pattern="*.csv", formats=["csv"])


def test_missing_root_is_not_treated_as_empty(tmp_path: Path) -> None:
    adapter = LocalStorageAdapter(tmp_path / "missing-root", "inbox")

    with pytest.raises(StorageUnavailableError):
        adapter.list_objects(pattern="*.csv", formats=["csv"])


@pytest.mark.parametrize("reference", ["../outside", "/etc", "inbox/../../outside"])
def test_location_reference_cannot_escape_root(tmp_path: Path, reference: str) -> None:
    root = tmp_path / "root"
    root.mkdir()

    with pytest.raises(StoragePathError):
        LocalStorageAdapter(root, reference)


@pytest.mark.parametrize("key", ["../secret.csv", "/etc/passwd"])
def test_object_key_cannot_escape_location(tmp_path: Path, key: str) -> None:
    root = tmp_path / "root"
    inbox = root / "inbox"
    inbox.mkdir(parents=True)
    adapter = LocalStorageAdapter(root, "inbox")

    with pytest.raises(StoragePathError):
        adapter.stat(key)


@pytest.mark.parametrize("pattern", ["../*.csv", "/etc/*.csv"])
def test_file_pattern_cannot_escape_location(tmp_path: Path, pattern: str) -> None:
    root = tmp_path / "root"
    inbox = root / "inbox"
    inbox.mkdir(parents=True)
    adapter = LocalStorageAdapter(root, "inbox")

    with pytest.raises(StoragePathError):
        adapter.list_objects(pattern=pattern, formats=["csv"])


def test_symlink_pointing_outside_location_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    inbox = root / "inbox"
    inbox.mkdir(parents=True)
    outside = tmp_path / "outside.csv"
    outside.write_text("a,b\n", encoding="utf-8")
    os.symlink(outside, inbox / "link.csv")

    adapter = LocalStorageAdapter(root, "inbox")

    with pytest.raises(StoragePathError):
        adapter.list_objects(pattern="*.csv", formats=["csv"])


def test_factory_uses_configured_root(settings, tmp_path: Path) -> None:
    root = tmp_path / "root"
    inbox = root / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "ticks.csv").write_text("a\n", encoding="utf-8")
    settings.INGESTION_LOCAL_ROOT = str(root)

    adapter = get_storage_adapter("local", "inbox")

    assert [obj.key for obj in adapter.list_objects(pattern="*.csv", formats=["csv"])] == [
        "ticks.csv"
    ]


def test_s3_backend_is_not_silently_mapped_to_local(settings, tmp_path: Path) -> None:
    settings.INGESTION_LOCAL_ROOT = str(tmp_path)

    with pytest.raises(UnsupportedStorageBackendError):
        get_storage_adapter("s3", "bucket/prefix")
