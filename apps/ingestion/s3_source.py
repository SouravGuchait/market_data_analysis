"""List and open objects in the configured S3 location.

The bucket, prefix and region come from environment settings. Access keys stay
in the process environment and are never accepted from an API request.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import boto3
from django.conf import settings

from .storage import ByteStream, StorageError, StorageUnavailableError


class _BodyStream:
    """Typed reader around the S3 response body."""

    def __init__(self, body: object) -> None:
        read = getattr(body, "read", None)
        if not callable(read):
            raise StorageError("S3 did not return a readable object.")
        self._read = read

    def read(self, size: int = -1) -> bytes:
        chunk = self._read(size)
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


def _bucket() -> str:
    return str(settings.INGESTION_S3_BUCKET).strip()


def _region() -> str:
    return str(settings.INGESTION_S3_REGION).strip()


def _client() -> object:
    region = _region()
    if region:
        return boto3.client("s3", region_name=region)
    return boto3.client("s3")


def _call(target: object, method_name: str, /, *args: str, **kwargs: str) -> object:
    method = getattr(target, method_name, None)
    if not callable(method):
        raise StorageError("The S3 client does not provide the requested operation.")
    result = method(*args, **kwargs)
    return result if result is not None else {}


def list_remote_objects() -> list[RemoteObject]:
    """Return files under the configured bucket prefix."""

    bucket = _bucket()
    if not bucket:
        raise StorageUnavailableError("S3 bucket is not configured.")
    prefix = str(settings.INGESTION_S3_PREFIX).strip().strip("/")
    request: dict[str, str] = {"Bucket": bucket}
    if prefix:
        request["Prefix"] = f"{prefix}/"
    paginator = _call(_client(), "get_paginator", "list_objects_v2")
    pages = _call(paginator, "paginate", **request)
    if not isinstance(pages, Iterable):
        raise StorageError("S3 did not return a page listing.")
    objects: list[RemoteObject] = []
    for page in pages:
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


def _remote_fields(item: object) -> tuple[str, int] | None:
    if not isinstance(item, dict):
        return None
    key = item.get("Key")
    size = item.get("Size")
    if not isinstance(key, str) or key.endswith("/") or not isinstance(size, int):
        return None
    return key, size


def _remote_object(item: object) -> RemoteObject | None:
    fields = _remote_fields(item)
    if fields is None:
        return None
    key, size = fields
    name = key.rsplit("/", 1)[-1]
    if not name:
        return None
    return RemoteObject(key=key, name=name, size_bytes=size)
