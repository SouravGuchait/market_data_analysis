"""List and open objects in the configured S3 location.

The bucket, prefix and region come from environment settings. Access keys stay
in the process environment and are never accepted from an API request.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings

from .storage import ByteStream, StorageError, StorageUnavailableError

_ERROR_RESPONSES: dict[str, tuple[type[StorageError], str]] = {
    "AccessDenied": (StorageError, "S3 denied access to the configured location."),
    "AccessDeniedException": (StorageError, "S3 denied access to the configured location."),
    "NoSuchBucket": (
        StorageUnavailableError,
        "The configured S3 bucket or object does not exist.",
    ),
    "NoSuchKey": (StorageUnavailableError, "The configured S3 bucket or object does not exist."),
}


class _BodyStream:
    """Typed reader around the S3 response body."""

    def __init__(self, body: object) -> None:
        read = getattr(body, "read", None)
        if not callable(read):
            raise StorageError("S3 did not return a readable object.")
        self._read = read

    def read(self, size: int = -1) -> bytes:
        try:
            chunk: object = self._read(size)
        except (BotoCoreError, ClientError) as exc:
            raise _s3_error(exc) from exc
        if isinstance(chunk, str):
            return chunk.encode()
        if isinstance(chunk, bytes):
            return chunk
        raise StorageError("S3 returned an unreadable object body.")


@dataclass(frozen=True)
class RemoteObject:
    """One S3 object under the configured prefix."""

    key: str
    name: str
    size_bytes: int
    modified_at: datetime


def _prefix() -> str:
    return str(settings.INGESTION_S3_PREFIX).strip().strip("/")


def _bucket() -> str:
    return str(settings.INGESTION_S3_BUCKET).strip()


def _region() -> str:
    return str(settings.INGESTION_S3_REGION).strip()


def _client() -> object:
    region = _region()
    if not region:
        return boto3.client("s3")
    return boto3.client("s3", region_name=region)


def _error_code(exc: ClientError) -> str:
    error = exc.response.get("Error")
    code = error.get("Code") if isinstance(error, dict) else None
    return code if isinstance(code, str) and code else "UnknownError"


def _s3_error(exc: BotoCoreError | ClientError) -> StorageError:
    if not isinstance(exc, ClientError):
        return StorageError("The S3 request could not be completed.")
    code = _error_code(exc)
    error_type, message = _ERROR_RESPONSES.get(
        code,
        (StorageError, f"The S3 request failed with {code}."),
    )
    return error_type(message)


def _call(target: object, method_name: str, /, *args: str, **kwargs: str) -> object:
    method = getattr(target, method_name, None)
    if not callable(method):
        raise StorageError("The S3 client does not provide the requested operation.")
    try:
        return method(*args, **kwargs)
    except (BotoCoreError, ClientError) as exc:
        raise _s3_error(exc) from exc


def _iter_pages(pages: Iterable[object]) -> Iterator[object]:
    """Yield listing pages, translating failures raised lazily during iteration."""

    try:
        yield from pages
    except (BotoCoreError, ClientError) as exc:
        raise _s3_error(exc) from exc


def list_remote_objects() -> list[RemoteObject]:
    """Return files under the configured bucket prefix."""

    bucket = _bucket()
    if not bucket:
        raise StorageUnavailableError("S3 bucket is not configured.")
    prefix = _prefix()
    request: dict[str, str] = {"Bucket": bucket}
    if prefix:
        request["Prefix"] = f"{prefix}/"
    paginator = _call(_client(), "get_paginator", "list_objects_v2")
    pages = _call(paginator, "paginate", **request)
    if not isinstance(pages, Iterable):
        raise StorageError("S3 did not return a page listing.")
    objects: list[RemoteObject] = []
    for page in _iter_pages(pages):
        objects.extend(_page_objects(page))
    return objects


def open_remote_object(key: str) -> ByteStream:
    """Open one configured object for a streaming read."""

    bucket = _bucket()
    if not bucket:
        raise StorageUnavailableError("S3 bucket is not configured.")
    response = _call(_client(), "get_object", Bucket=bucket, Key=key)
    if not isinstance(response, dict):
        raise StorageError("S3 did not return an object body.")
    return _BodyStream(response.get("Body"))


def _page_objects(page: object) -> list[RemoteObject]:
    if not isinstance(page, dict):
        return []
    contents = page.get("Contents", [])
    if not isinstance(contents, list):
        return []
    objects: list[RemoteObject] = []
    for item in contents:
        remote = _remote_object(item)
        if remote is not None:
            objects.append(remote)
    return objects


def _remote_fields(item: object) -> tuple[str, int, datetime] | None:
    if not isinstance(item, dict):
        return None
    key = item.get("Key")
    size = item.get("Size")
    modified_at = item.get("LastModified")
    if (
        not isinstance(key, str)
        or key.endswith("/")
        or not isinstance(size, int)
        or not isinstance(modified_at, datetime)
    ):
        return None
    return key, size, modified_at


def _remote_object(item: object) -> RemoteObject | None:
    fields = _remote_fields(item)
    if fields is None:
        return None
    key, size, modified_at = fields
    name = key.rsplit("/", 1)[-1]
    if not name:
        return None
    return RemoteObject(key=key, name=name, size_bytes=size, modified_at=modified_at)
