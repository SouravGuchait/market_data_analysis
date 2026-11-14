"""Copy every supported file from the configured local directory and S3 prefix."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User

from .models import SUPPORTED_FILE_FORMATS, StorageBackend, StorageFile
from .s3_source import RemoteObject, list_remote_objects, open_remote_object
from .storage import ByteStream, LocalStorageAdapter, StorageError, StorageLimitError
from .user_services import IngestionRequestError, _checked_filename, _checked_prefix, _user_location

IMPORT_BACKENDS = ("local", "s3")


@dataclass(frozen=True)
class ImportSkip:
    name: str
    reason: str


@dataclass
class ImportBatch:
    stored: list[StorageFile]
    skipped: list[ImportSkip]


class _PathStream:
    def __init__(self, path: Path) -> None:
        self._handle = path.open("rb")

    def read(self, size: int = -1) -> bytes:
        return self._handle.read(size)


class LocalImportFile:
    """A file that already sits under the configured local root."""

    def __init__(self, path: Path) -> None:
        self.name = path.name
        self.size_bytes = path.stat().st_size
        self.content_hash = _hash_file(path)
        self._path = path

    def open(self) -> ByteStream:
        return _PathStream(self._path)


class S3ImportFile:
    """An object listed from the configured S3 prefix."""

    def __init__(self, item: RemoteObject) -> None:
        self.name = item.name
        self.size_bytes = item.size_bytes
        self.content_hash = ""
        self._key = item.key

    def open(self) -> ByteStream:
        return open_remote_object(self._key)


def import_configured_files(
    owner: User,
    backends: Sequence[str],
    storage_prefix: str,
) -> ImportBatch:
    """Store every configured file for this user in one request."""

    prefix = _checked_prefix(storage_prefix)
    selected = _selected_backends(backends)
    files = _source_files(selected)
    limit = settings.INGESTION_IMPORT_MAX_FILES
    if len(files) > limit:
        raise _limit_error(limit)

    adapter = LocalStorageAdapter(settings.INGESTION_LOCAL_ROOT, "")
    batch = ImportBatch(stored=[], skipped=[])
    for source in files:
        record, reason = _store_source(adapter, owner, source, prefix)
        if record is None:
            batch.skipped.append(ImportSkip(name=source.name, reason=reason))
        else:
            batch.stored.append(record)
    return batch


def _selected_backends(backends: Sequence[str]) -> list[str]:
    selected = list(dict.fromkeys(backends))
    if not selected:
        raise IngestionRequestError(
            "Validation failed",
            {"storage_backends": ["Choose local, s3, or both."]},
        )
    unknown = [item for item in selected if item not in IMPORT_BACKENDS]
    if unknown:
        raise IngestionRequestError(
            "Validation failed",
            {"storage_backends": ["Storage backends must be local or s3."]},
        )
    return selected


def _source_files(backends: Sequence[str]) -> list[LocalImportFile | S3ImportFile]:
    files: list[LocalImportFile | S3ImportFile] = []
    if "local" in backends:
        files.extend(_local_files())
    if "s3" in backends:
        files.extend(S3ImportFile(item) for item in list_remote_objects())
    return files


def _existing_local_root() -> Path:
    root = Path(settings.INGESTION_LOCAL_ROOT).expanduser().resolve()
    if root.is_dir():
        return root
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise IngestionRequestError(
            "Validation failed",
            {"storage_backends": ["The configured local directory does not exist."]},
        ) from exc
    return root


def _local_files() -> list[LocalImportFile]:
    root = _existing_local_root()
    files: list[LocalImportFile] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if not path.is_file():
            continue
        try:
            relative = path.resolve().relative_to(root)
        except ValueError:
            continue
        if relative.parts[0] == "users":
            continue
        if path.suffix.lower().lstrip(".") not in SUPPORTED_FILE_FORMATS:
            continue
        files.append(LocalImportFile(path))
    return files


def _source_problem(source: LocalImportFile | S3ImportFile) -> str:
    if source.size_bytes < 1:
        return "The file is empty."
    if source.size_bytes > settings.INGESTION_MAX_UPLOAD_BYTES:
        return "The file exceeds the configured maximum size."
    return ""


def _accepted_name(source: LocalImportFile | S3ImportFile) -> tuple[str, str] | str:
    try:
        return _checked_filename(source.name)
    except IngestionRequestError as exc:
        return exc.errors["files"][0]


def _write_source(
    adapter: LocalStorageAdapter,
    source: LocalImportFile | S3ImportFile,
    storage_key: str,
) -> str:
    try:
        adapter.save(source.open(), storage_key, max_bytes=settings.INGESTION_MAX_UPLOAD_BYTES)
    except StorageLimitError:
        return "The file exceeds the configured maximum size."
    except StorageError:
        return "The file could not be stored."
    return ""


def _store_source(
    adapter: LocalStorageAdapter,
    owner: User,
    source: LocalImportFile | S3ImportFile,
    storage_prefix: str,
) -> tuple[StorageFile | None, str]:
    accepted = _accepted_name(source)
    if isinstance(accepted, str):
        return None, accepted
    problem = _source_problem(source)
    if problem:
        return None, problem
    return _copy_source(adapter, owner, source, storage_prefix, accepted)


def _copy_source(
    adapter: LocalStorageAdapter,
    owner: User,
    source: LocalImportFile | S3ImportFile,
    storage_prefix: str,
    accepted: tuple[str, str],
) -> tuple[StorageFile | None, str]:
    original_name, extension = accepted
    storage_key = f"{_user_location(owner, storage_prefix)}/{original_name}"
    existing = StorageFile.objects.filter(owner=owner, storage_key=storage_key).first()
    same_hash = existing is not None and source.content_hash == existing.content_hash
    write_error = (
        "Already stored."
        if source.content_hash and same_hash
        else _write_source(adapter, source, storage_key)
    )
    if write_error:
        return None, write_error
    record = _save_record(owner, existing, adapter, (storage_key, original_name, extension))
    return (None, "Already stored.") if record is None else (record, "")


def _save_record(
    owner: User,
    existing: StorageFile | None,
    adapter: LocalStorageAdapter,
    stored_name: tuple[str, str, str],
) -> StorageFile | None:
    storage_key, original_name, extension = stored_name
    content_hash = adapter.content_hash(storage_key)
    size_bytes = adapter.stat(storage_key).size_bytes
    if existing is not None and existing.content_hash == content_hash:
        return None
    if existing is None:
        return StorageFile.objects.create(
            owner=owner,
            storage_backend=StorageBackend.LOCAL,
            storage_key=storage_key,
            original_filename=original_name,
            file_size=size_bytes,
            content_hash=content_hash,
            detected_format=extension,
        )
    existing.file_size = size_bytes
    existing.content_hash = content_hash
    existing.detected_format = extension
    existing.original_filename = original_name
    existing.is_active = True
    existing.save(
        update_fields=[
            "file_size",
            "content_hash",
            "detected_format",
            "original_filename",
            "is_active",
        ]
    )
    return existing


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(settings.INGESTION_HASH_CHUNK_SIZE_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _limit_error(limit: int) -> IngestionRequestError:
    return IngestionRequestError(
        "Validation failed",
        {"storage_backends": [f"Configured locations contain more than {limit} files."]},
    )
