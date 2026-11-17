"""S3 listing, object reads and botocore error translation."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

from apps.ingestion.s3_source import (
    list_remote_objects,
    open_remote_object,
)
from apps.ingestion.storage import StorageError, StorageUnavailableError

BUCKET = "mda-files-bucket"
PREFIX = "market-data"


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "denied"}}, "ListObjectsV2")


def _pages_raising(error: BotoCoreError | ClientError) -> Iterator[object]:
    """Return an iterator that fails when the listing is consumed."""
    raise error
    yield {}


class _ListingClient:
    """Minimal stand-in for the boto3 paginator surface."""

    def __init__(self, pages: Iterable[object]) -> None:
        self.pages = pages
        self.requests: list[dict[str, str]] = []

    def get_paginator(self, operation: str) -> _ListingPaginator:
        assert operation == "list_objects_v2"
        return _ListingPaginator(self)


class _ListingPaginator:
    def __init__(self, client: _ListingClient) -> None:
        self._client = client

    def paginate(self, **kwargs: str) -> Iterable[object]:
        self._client.requests.append(kwargs)
        return self._client.pages


class _ObjectClient:
    """Minimal stand-in for ``get_object``."""

    def __init__(self, response: object) -> None:
        self._response = response
        self.requests: list[dict[str, str]] = []

    def get_object(self, **kwargs: str) -> object:
        self.requests.append(kwargs)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class _FailingBody:
    def read(self, size: int = -1) -> bytes:
        raise _client_error("RequestTimeout")


@pytest.fixture
def s3_settings(settings) -> None:
    settings.INGESTION_S3_BUCKET = BUCKET
    settings.INGESTION_S3_PREFIX = f"/{PREFIX}/"
    settings.INGESTION_S3_REGION = "eu-north-1"


@pytest.mark.usefixtures("s3_settings")
def test_listing_uses_the_configured_bucket_and_prefix() -> None:
    modified_at = datetime(2026, 11, 17, 20, 0, tzinfo=UTC)
    client = _ListingClient(
        [
            {
                "Contents": [
                    {"Key": f"{PREFIX}/a.csv", "Size": 10, "LastModified": modified_at},
                    {"Key": f"{PREFIX}/", "Size": 0, "LastModified": modified_at},
                ]
            }
        ]
    )

    with patch("apps.ingestion.s3_source._client", return_value=client):
        objects = list_remote_objects()

    assert [item.key for item in objects] == [f"{PREFIX}/a.csv"]
    assert objects[0].name == "a.csv"
    assert objects[0].size_bytes == 10
    assert client.requests == [{"Bucket": BUCKET, "Prefix": f"{PREFIX}/"}]


@pytest.mark.usefixtures("s3_settings")
def test_listing_translates_access_denied() -> None:
    client = _ListingClient(_pages_raising(_client_error("AccessDenied")))

    with (
        patch("apps.ingestion.s3_source._client", return_value=client),
        pytest.raises(StorageError) as raised,
    ):
        list_remote_objects()

    assert str(raised.value) == "S3 denied access to the configured location."


@pytest.mark.usefixtures("s3_settings")
def test_listing_translates_missing_credentials() -> None:
    client = _ListingClient(_pages_raising(NoCredentialsError()))

    with (
        patch("apps.ingestion.s3_source._client", return_value=client),
        pytest.raises(StorageError) as raised,
    ):
        list_remote_objects()

    assert str(raised.value) == "The S3 request could not be completed."


@pytest.mark.usefixtures("s3_settings")
def test_listing_reports_an_unhandled_client_error_code() -> None:
    client = _ListingClient(_pages_raising(_client_error("InvalidAccessKeyId")))

    with (
        patch("apps.ingestion.s3_source._client", return_value=client),
        pytest.raises(StorageError) as raised,
    ):
        list_remote_objects()

    assert str(raised.value) == "The S3 request failed with InvalidAccessKeyId."


@pytest.mark.usefixtures("s3_settings")
def test_open_translates_a_missing_object() -> None:
    client = _ObjectClient(_client_error("NoSuchKey"))

    with (
        patch("apps.ingestion.s3_source._client", return_value=client),
        pytest.raises(StorageUnavailableError),
    ):
        open_remote_object(f"{PREFIX}/gone.csv")

    assert client.requests == [{"Bucket": BUCKET, "Key": f"{PREFIX}/gone.csv"}]


@pytest.mark.usefixtures("s3_settings")
def test_open_translates_a_failing_body_read() -> None:
    client = _ObjectClient({"Body": _FailingBody()})

    with patch("apps.ingestion.s3_source._client", return_value=client):
        stream = open_remote_object(f"{PREFIX}/a.csv")
        with pytest.raises(StorageError) as raised:
            stream.read(16)

    assert str(raised.value) == "The S3 request failed with RequestTimeout."
