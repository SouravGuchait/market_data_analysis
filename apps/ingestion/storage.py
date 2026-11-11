"""Storage abstraction for ingestion discovery.

The discovery service only depends on the :class:`StorageAdapter` contract, so
the local filesystem implementation can later be joined by EFS and S3 adapters
without changing ingestion business logic.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, Protocol, runtime_checkable

from django.conf import settings

from .models import StorageBackend

logger = logging.getLogger("apps.ingestion.storage")

DEFAULT_HASH_CHUNK_SIZE_BYTES = 1024 * 1024


class StorageError(Exception):
    """Base class for storage adapter failures."""


class UnsupportedStorageBackendError(StorageError):
    """Raised when no adapter is implemented for a configured backend."""


class StoragePathError(StorageError):
    """Raised when an object reference escapes its configured location."""


class StorageUnavailableError(StorageError):
    """Raised when a configured storage location cannot be used."""


@dataclass(frozen=True)
class StorageObject:
    """Immutable metadata for one discoverable object."""

    key: str
    name: str
    size_bytes: int
    modified_at: datetime
    backend: str


@runtime_checkable
class StorageAdapter(Protocol):
    """Contract shared by local, EFS and future S3 adapters."""

    backend: str

    def list_objects(
        self,
        *,
        pattern: str,
        formats: Sequence[str],
    ) -> list[StorageObject]:
        """Return files matching the pattern and supported extensions."""
        ...

    def stat(self, key: str) -> StorageObject:
        """Return current metadata for an object."""
        ...

    def content_hash(self, key: str) -> str:
        """Return a SHA-256 content identity for an object."""
        ...

    def open(self, key: str) -> BinaryIO:
        """Open an object for streaming reads; the caller closes it."""
        ...

    def read_bytes(self, key: str) -> bytes:
        """Read an object completely. Prefer ``open`` for large files."""
        ...


def _resolve_location(root: Path, location_reference: str) -> Path:
    reference = (location_reference or "").strip()
    reference_path = Path(reference)

    if reference_path.is_absolute():
        raise StoragePathError("Local storage locations must be relative to the root.")
    if ".." in reference_path.parts:
        raise StoragePathError("Local storage locations cannot traverse outside the root.")

    resolved = (root / reference_path).resolve()
    if not resolved.is_relative_to(root):
        raise StoragePathError("Local storage location escapes the configured root.")
    return resolved


class LocalStorageAdapter:
    """Discover and read files under the configured local ingestion root."""

    backend = str(StorageBackend.LOCAL)

    def __init__(self, root: str | Path, location_reference: str) -> None:
        self.root = Path(root).expanduser().resolve()
        self.location = _resolve_location(self.root, location_reference)

    def _ensure_location(self) -> None:
        if not self.root.is_dir():
            raise StorageUnavailableError(
                "The configured ingestion root does not exist or is not a directory."
            )
        if not self.location.is_dir():
            raise StorageUnavailableError(
                "The configured rule location does not exist or is not a directory."
            )

    def _resolve_key(self, key: str) -> Path:
        key_path = Path((key or "").strip())
        if not key_path.parts:
            raise StoragePathError("An object key is required.")
        if key_path.is_absolute() or ".." in key_path.parts:
            raise StoragePathError(
                "Object keys cannot be absolute or traverse outside the location."
            )

        candidate = (self.location / key_path).resolve()
        if not candidate.is_relative_to(self.location):
            raise StoragePathError("Object key escapes the configured rule location.")
        return candidate

    def _as_object(self, path: Path) -> StorageObject:
        try:
            stat = path.stat()
        except OSError as exc:
            raise StorageError(f"Unable to read object metadata for {path.name!r}.") from exc

        return StorageObject(
            key=path.relative_to(self.location).as_posix(),
            name=path.name,
            size_bytes=stat.st_size,
            modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
            backend=self.backend,
        )

    def list_objects(
        self,
        *,
        pattern: str,
        formats: Sequence[str],
    ) -> list[StorageObject]:
        self._ensure_location()
        pattern_path = Path((pattern or "").strip())
        if not pattern_path.parts or pattern_path.is_absolute() or ".." in pattern_path.parts:
            raise StoragePathError(
                "File patterns cannot be empty, absolute or traverse outside the location."
            )

        allowed_formats = {str(value).lower().lstrip(".") for value in formats}
        if not allowed_formats:
            return []

        try:
            paths = sorted(
                self.location.glob(pattern),
                key=lambda path: (
                    path.name.casefold(),
                    path.relative_to(self.location).as_posix().casefold(),
                ),
            )
        except OSError as exc:
            raise StorageUnavailableError("Unable to list the configured rule location.") from exc

        objects: list[StorageObject] = []
        for path in paths:
            if not path.is_file():
                continue
            if not path.resolve().is_relative_to(self.location):
                raise StoragePathError("Discovered object escapes the configured rule location.")
            if path.suffix.lower().lstrip(".") not in allowed_formats:
                continue
            objects.append(self._as_object(path))
        return objects

    def stat(self, key: str) -> StorageObject:
        self._ensure_location()
        path = self._resolve_key(key)
        if not path.is_file():
            raise StorageError(f"Object {key!r} does not exist.")
        return self._as_object(path)

    def content_hash(self, key: str) -> str:
        chunk_size = int(
            getattr(settings, "INGESTION_HASH_CHUNK_SIZE_BYTES", DEFAULT_HASH_CHUNK_SIZE_BYTES)
        )
        if chunk_size < 1:
            chunk_size = DEFAULT_HASH_CHUNK_SIZE_BYTES

        digest = hashlib.sha256()
        with self.open(key) as stream:
            while chunk := stream.read(chunk_size):
                digest.update(chunk)
        return digest.hexdigest()

    def open(self, key: str) -> BinaryIO:
        self._ensure_location()
        path = self._resolve_key(key)
        if not path.is_file():
            raise StorageError(f"Object {key!r} does not exist.")
        try:
            return path.open("rb")
        except OSError as exc:
            raise StorageError(f"Unable to open object {key!r}.") from exc

    def read_bytes(self, key: str) -> bytes:
        with self.open(key) as stream:
            return stream.read()


def get_storage_adapter(storage_backend: str, location_reference: str) -> StorageAdapter:
    """Build the adapter for a configured backend.

    EFS is a mounted filesystem and deliberately reuses the local adapter. S3 is
    reserved for a later feature and fails loudly rather than falling back to a
    local path.
    """

    if storage_backend in {StorageBackend.LOCAL, StorageBackend.EFS}:
        return LocalStorageAdapter(settings.INGESTION_LOCAL_ROOT, location_reference)

    raise UnsupportedStorageBackendError(f"Storage backend {storage_backend!r} is not implemented.")
