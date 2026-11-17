"""Authenticated file upload, rules, import queue and retry."""

from __future__ import annotations

from datetime import timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest
from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import Resolver404, resolve, reverse
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.test import APIClient

from apps.audit.models import AuditAction, AuditEvent
from apps.ingestion.models import (
    Ingestion,
    IngestionRule,
    IngestionState,
    StorageBackend,
    StorageFile,
)
from apps.ingestion.s3_source import RemoteObject
from apps.ingestion.storage import StorageError
from apps.ingestion.tasks import scan_enabled_ingestion_rules

pytestmark = pytest.mark.django_db

PASSWORD = "Ingestion!User_Password_9274"


class _DelayedTask:
    def __init__(self, task_id: str) -> None:
        self.id = task_id


@pytest.fixture
def user() -> User:
    return User.objects.create_user(username="alice", email="alice@example.com", password=PASSWORD)


@pytest.fixture
def other_user() -> User:
    return User.objects.create_user(username="bob", email="bob@example.com", password=PASSWORD)


@pytest.fixture
def auth_client(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def other_client(other_user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=other_user)
    return client


def _csv(name: str, body: bytes | None = None) -> SimpleUploadedFile:
    payload = body if body is not None else b"symbol,close\nACME,10\n"
    return SimpleUploadedFile(name, payload, content_type="text/csv")


def _rule_body(name: str = "Daily Market CSV", prefix: str = "market") -> dict[str, object]:
    return {
        "name": name,
        "data_type": "DAILY_OHLCV",
        "storage_backend": "LOCAL",
        "storage_prefix": prefix,
        "file_pattern": "*.csv",
        "chunk_size": 5000,
        "is_enabled": True,
        "parser_config": {"format": "CSV", "delimiter": ",", "encoding": "utf-8", "header": True},
        "instrument_mapping": {"instrument_identifier": "symbol"},
        "exchange_mapping": {"exchange_identifier": "exchange"},
    }


def _response(result: object) -> Response:
    if isinstance(result, Response):
        return result
    raise AssertionError("Expected a DRF response.")


def _payload(response: Response) -> dict[str, object]:
    body = response.data
    if not isinstance(body, dict):
        raise AssertionError("Expected a JSON object.")
    return {str(key): value for key, value in body.items()}


def _text_field(payload: dict[str, object], key: str) -> str:
    value = payload[key]
    if not isinstance(value, str):
        raise AssertionError(f"Expected {key} to be a string.")
    return value


def _data_dict(response: Response) -> dict[str, object]:
    data = _payload(response)["data"]
    if not isinstance(data, dict):
        raise AssertionError("Expected a JSON object in data.")
    return {str(key): value for key, value in data.items()}


def _import_files(
    client: APIClient,
    root: Path,
    *files: SimpleUploadedFile,
    prefix: str = "market",
) -> dict[str, object]:
    """Place files in the configured directory and import that directory."""

    for upload in files:
        filename = upload.name or "upload.bin"
        (root / Path(filename).name).write_bytes(b"".join(upload.chunks()))
    response = _response(
        client.post(
            reverse("ingestion-file-import"),
            {"storage_backends": ["local"], "storage_prefix": prefix},
            format="json",
        )
    )
    assert response.status_code == 201, response.content
    stored = _data_dict(response)["stored"]
    if not isinstance(stored, list):
        raise AssertionError("Expected a list of stored files.")
    return {"data": stored}


def _create_rule(client: APIClient, body: dict[str, object] | None = None) -> dict[str, object]:
    response = _response(
        client.post(reverse("ingestion-rule-list"), body or _rule_body(), format="json")
    )
    assert response.status_code == 201, response.content
    return _data_dict(response)


def test_jsonl_import_is_accepted(auth_client: APIClient, ingestion_root: Path) -> None:
    body = _import_files(
        auth_client,
        ingestion_root,
        SimpleUploadedFile("ticks.jsonl", b'{"price":1}\n', content_type="application/jsonl"),
    )
    stored = body["data"]
    assert isinstance(stored, list)
    item = stored[0]
    assert isinstance(item, dict)
    assert item.get("detected_format") == "jsonl"


def test_import_requires_authentication() -> None:
    response = _response(
        APIClient().post(
            reverse("ingestion-file-import"),
            {"storage_backends": ["local"]},
            format="json",
        )
    )
    assert response.status_code == 401


def test_import_stores_multiple_files_under_the_user_directory(
    auth_client: APIClient,
    user: User,
    ingestion_root,
    settings,
) -> None:
    body = _import_files(
        auth_client,
        ingestion_root,
        _csv("alpha.csv"),
        _csv("beta.csv", b"symbol,close\nBETA,2\n"),
    )
    stored_files = body["data"]
    assert isinstance(stored_files, list)
    assert len(stored_files) == 2
    assert StorageFile.objects.filter(owner=user).count() == 2
    first_id = ""
    for item in stored_files:
        assert isinstance(item, dict)
        key = item.get("storage_key")
        file_id = item.get("id")
        assert isinstance(key, str)
        assert isinstance(file_id, str)
        assert key.startswith(f"users/{user.pk}/files/")
        assert settings.INGESTION_LOCAL_ROOT not in key
        assert (Path(settings.INGESTION_SYSTEM_ROOT) / key).is_file()
        assert not (ingestion_root / key).exists()
        assert (ingestion_root / "alpha.csv").is_file()
        first_id = first_id or file_id
    event = AuditEvent.objects.get(actor=user, resource_id=first_id)
    assert event.action == AuditAction.INGESTION_STARTED
    assert event.metadata["scope"] == "configured_import"


def test_import_skips_unsafe_files(
    auth_client: APIClient,
    user: User,
    ingestion_root,
    settings,
) -> None:
    settings.INGESTION_MAX_UPLOAD_BYTES = 8
    (ingestion_root / "big.csv").write_bytes(b"0123456789")
    (ingestion_root / "notes.txt").write_bytes(b"not,allowed\n")
    outside = ingestion_root.parent / "secret.csv"
    outside.write_bytes(b"symbol,close\nA,1\n")
    (ingestion_root / "linked.csv").symlink_to(outside)
    oversized = _response(
        auth_client.post(
            reverse("ingestion-file-import"),
            {"storage_backends": ["local"], "storage_prefix": "market"},
            format="json",
        )
    )
    assert oversized.status_code == 201
    assert StorageFile.objects.filter(owner=user).count() == 0
    assert not list(ingestion_root.rglob("*secret.csv"))
    skipped = _data_dict(oversized)["skipped"]
    assert isinstance(skipped, list)
    assert any(isinstance(item, dict) and item.get("name") == "big.csv" for item in skipped)

    settings.INGESTION_MAX_UPLOAD_BYTES = 1024
    prefix = _response(
        auth_client.post(
            reverse("ingestion-file-import"),
            {"storage_backends": ["local"], "storage_prefix": "../outside"},
            format="json",
        )
    )
    assert prefix.status_code == 400


def test_rules_are_owner_scoped(
    auth_client: APIClient,
    other_client: APIClient,
    user: User,
    ingestion_root,
    settings,
) -> None:
    created = _create_rule(auth_client)
    assert created["storage_prefix"] == "files"
    assert "location_reference" not in created
    system_files = Path(settings.INGESTION_SYSTEM_ROOT) / "users" / str(user.pk) / "files"
    assert system_files.is_dir()
    assert not (ingestion_root / "users").exists()

    own_list = _response(auth_client.get(reverse("ingestion-rule-list")))
    assert own_list.status_code == 200
    assert _data_dict(own_list)["count"] == 1

    foreign = _response(
        other_client.get(
            reverse("ingestion-rule-detail", kwargs={"rule_id": _text_field(created, "id")})
        )
    )
    assert foreign.status_code == 404

    patched = _response(
        auth_client.patch(
            reverse("ingestion-rule-detail", kwargs={"rule_id": _text_field(created, "id")}),
            {"name": "Renamed"},
            format="json",
        )
    )
    assert patched.status_code == 200
    assert _data_dict(patched)["name"] == "Renamed"
    assert (
        AuditEvent.objects.filter(
            actor=user,
            action=AuditAction.INGESTION_RULE_CHANGED,
            resource_id=_text_field(created, "id"),
        ).count()
        == 2
    )


def test_rule_create_builds_a_missing_system_root(
    auth_client: APIClient,
    ingestion_root: Path,
    settings,
) -> None:
    system = Path(settings.INGESTION_SYSTEM_ROOT)
    system.rmdir()

    created = _response(
        auth_client.post(reverse("ingestion-rule-list"), _rule_body(), format="json")
    )

    assert created.status_code == 201
    assert system.is_dir()
    assert not (ingestion_root / "users").exists()


def test_import_creates_a_missing_local_directory(
    auth_client: APIClient, ingestion_root: Path
) -> None:
    ingestion_root.rmdir()

    imported = _response(
        auth_client.post(
            reverse("ingestion-file-import"),
            {"storage_backends": ["local"], "storage_prefix": "market"},
            format="json",
        )
    )

    assert imported.status_code == 201
    assert ingestion_root.is_dir()


def test_import_gives_each_file_its_own_ingestion(
    auth_client: APIClient,
    ingestion_root,
) -> None:
    _create_rule(auth_client)
    files = [
        _csv(f"market-{index:02}.csv", f"symbol,close\nA,{index}\n".encode()) for index in range(11)
    ]
    _import_files(auth_client, ingestion_root, *files)

    assert Ingestion.objects.count() == 11
    assert Ingestion.objects.values("id").distinct().count() == 11


def test_filters_pagination_and_cross_user_detail(
    auth_client: APIClient,
    other_client: APIClient,
    ingestion_root,
) -> None:
    daily_body = _rule_body("Daily", "files")
    daily_body["file_pattern"] = "daily.csv"
    _create_rule(auth_client, daily_body)
    tick_body = _rule_body("Ticks", "files")
    tick_body["data_type"] = "TICK_DATA"
    tick_body["file_pattern"] = "ticks.csv"
    _create_rule(auth_client, tick_body)
    _import_files(auth_client, ingestion_root, _csv("daily.csv"), prefix="files")
    _import_files(
        auth_client, ingestion_root, _csv("ticks.csv", b"price,qty\n1,2\n"), prefix="files"
    )

    filtered = _response(
        auth_client.get(
            reverse("ingestion-list"),
            {"data_type": "TICK_DATA", "status": "queued", "filename": "ticks"},
        )
    )
    assert filtered.status_code == 200
    payload = _data_dict(filtered)
    results = payload["results"]
    assert isinstance(results, list)
    assert payload["count"] == 1
    first_result = results[0]
    assert isinstance(first_result, dict)
    assert first_result.get("filename") == "ticks.csv"
    assert first_result.get("data_type") == "TICK_DATA"
    assert "object_key" not in first_result
    ingestion_id = first_result.get("id")
    assert isinstance(ingestion_id, str)

    page = _response(auth_client.get(reverse("ingestion-list"), {"page_size": 1}))
    page_data = _data_dict(page)
    page_results = page_data["results"]
    assert page_data["count"] == 2
    assert isinstance(page_results, list)
    assert len(page_results) == 1
    hidden = _response(
        other_client.get(reverse("ingestion-detail", kwargs={"ingestion_id": ingestion_id}))
    )
    assert hidden.status_code == 404
    hidden_retry = _response(
        other_client.post(reverse("ingestion-retry", kwargs={"ingestion_id": ingestion_id}))
    )
    assert hidden_retry.status_code == 404
    rejections = _response(
        auth_client.get(reverse("ingestion-rejections", kwargs={"ingestion_id": ingestion_id}))
    )
    assert rejections.status_code == 200
    assert _data_dict(rejections)["results"] == []


def test_retry_is_limited_to_the_owner_failed_ingestion(
    auth_client: APIClient,
    ingestion_root,
    django_capture_on_commit_callbacks,
) -> None:
    _create_rule(auth_client)
    _import_files(auth_client, ingestion_root, _csv("retry.csv"))
    ingestion = Ingestion.objects.get()
    ingestion.state = IngestionState.FAILED
    ingestion.error_summary = "dispatch failed"
    ingestion.rows_read = 12
    ingestion.accepted_rows = 9
    ingestion.save(
        update_fields=["state", "error_summary", "rows_read", "accepted_rows", "updated_at"]
    )

    with (
        patch(
            "apps.ingestion.tasks.process_ingestion_file.delay",
            return_value=_DelayedTask("retry-1"),
        ),
        django_capture_on_commit_callbacks(execute=True),
    ):
        response = _response(
            auth_client.post(reverse("ingestion-retry", kwargs={"ingestion_id": ingestion.pk}))
        )

    assert response.status_code == 200
    ingestion.refresh_from_db()
    assert ingestion.state == IngestionState.QUEUED
    assert ingestion.celery_task_id == "retry-1"
    assert ingestion.rows_read == 12
    assert ingestion.accepted_rows == 9
    assert ingestion.error_summary == "dispatch failed"

    ingestion.state = IngestionState.SUCCESS
    ingestion.save(update_fields=["state", "updated_at"])
    rejected = _response(
        auth_client.post(reverse("ingestion-retry", kwargs={"ingestion_id": ingestion.pk}))
    )
    assert rejected.status_code == 400


def test_scheduler_does_not_scan_user_owned_rules(
    auth_client: APIClient,
    user: User,
    ingestion_root,
    rule: IngestionRule,
) -> None:
    _import_files(auth_client, ingestion_root, _csv("owned.csv"))
    _create_rule(auth_client)
    results = scan_enabled_ingestion_rules()

    assert [item["rule_name"] for item in results] == [rule.name]
    assert Ingestion.objects.filter(owner=user).count() == 0


def test_disabled_rule_does_not_queue_an_import(
    auth_client: APIClient,
    ingestion_root,
) -> None:
    rule = _create_rule(auth_client)
    IngestionRule.objects.filter(pk=_text_field(rule, "id")).update(is_enabled=False)
    _import_files(auth_client, ingestion_root, _csv("held.csv"))
    assert Ingestion.objects.count() == 0


def test_import_copies_every_configured_local_file(
    auth_client: APIClient,
    user: User,
    ingestion_root,
    settings,
) -> None:
    (ingestion_root / "synthetic_market_clean.csv").write_bytes(b"symbol,close\nACME,1\n")
    (ingestion_root / "synthetic_market_clean.json").write_text(
        '[{"symbol":"ACME"}]',
        encoding="utf-8",
    )
    hidden = ingestion_root / "users" / "other"
    hidden.mkdir(parents=True)
    (hidden / "secret.csv").write_bytes(b"symbol,close\nNO,1\n")
    (ingestion_root / "notes.txt").write_text("skip", encoding="utf-8")

    response = _response(
        auth_client.post(
            reverse("ingestion-file-import"),
            {"storage_backends": ["local"], "storage_prefix": "daily"},
            format="json",
        )
    )
    assert response.status_code == 201
    assert _data_dict(response)["scanned"] == {"local": 2, "s3": 0}
    stored = _data_dict(response)["stored"]
    assert isinstance(stored, list)
    names: list[str] = []
    for item in stored:
        if not isinstance(item, dict):
            continue
        filename = item.get("original_filename")
        if isinstance(filename, str):
            names.append(filename)
    names = sorted(names)
    assert names == ["synthetic_market_clean.csv", "synthetic_market_clean.json"]
    copied = (
        Path(settings.INGESTION_SYSTEM_ROOT)
        / "users"
        / str(user.pk)
        / "files"
        / "synthetic_market_clean.csv"
    )
    assert copied.is_file()

    again = _response(
        auth_client.post(
            reverse("ingestion-file-import"),
            {"storage_backends": ["local"], "storage_prefix": "daily"},
            format="json",
        )
    )
    assert _data_dict(again)["stored"] == []
    skipped = _data_dict(again)["skipped"]
    assert isinstance(skipped, list)
    assert len(skipped) == 2


def test_import_copies_configured_s3_objects(
    auth_client: APIClient,
    ingestion_root,
    settings,
) -> None:
    settings.INGESTION_S3_BUCKET = "market-bucket"
    settings.INGESTION_S3_PREFIX = "incoming"
    remote = RemoteObject(
        key="incoming/synthetic_ticks.csv",
        name="synthetic_ticks.csv",
        size_bytes=16,
        modified_at=timezone.now() - timedelta(days=2),
    )

    def open_remote(key: str) -> BytesIO:
        assert key == remote.key
        return BytesIO(b"price,size\n1,2\n")

    with (
        patch("apps.ingestion.imports.list_remote_objects", return_value=[remote]),
        patch("apps.ingestion.imports.open_remote_object", side_effect=open_remote),
    ):
        response = _response(
            auth_client.post(
                reverse("ingestion-file-import"),
                {"storage_backends": ["s3"], "storage_prefix": "ticks"},
                format="json",
            )
        )

    assert response.status_code == 201
    assert _data_dict(response)["scanned"] == {"local": 0, "s3": 1}
    stored = _data_dict(response)["stored"]
    assert isinstance(stored, list)
    first = stored[0]
    assert isinstance(first, dict)
    assert first.get("original_filename") == "synthetic_ticks.csv"
    assert StorageFile.objects.filter(original_filename="synthetic_ticks.csv").count() == 1
    assert (
        Path(settings.INGESTION_SYSTEM_ROOT) / first["storage_key"]
    ).stat().st_mtime == pytest.approx(remote.modified_at.timestamp(), abs=1)


def test_import_returns_400_when_s3_denies_access(
    auth_client: APIClient,
    ingestion_root,
    settings,
) -> None:
    settings.INGESTION_S3_BUCKET = "market-bucket"
    settings.INGESTION_S3_PREFIX = "market-data"

    with patch(
        "apps.ingestion.imports.list_remote_objects",
        side_effect=StorageError("S3 denied access to the configured location."),
    ):
        response = _response(
            auth_client.post(
                reverse("ingestion-file-import"),
                {"storage_backends": ["s3"], "storage_prefix": "files"},
                format="json",
            )
        )

    assert response.status_code == 400
    assert _text_field(_payload(response), "message") == "Unable to read the configured storage."


def test_import_rejects_s3_when_the_bucket_is_not_configured(
    auth_client: APIClient,
    ingestion_root,
    settings,
) -> None:
    settings.INGESTION_S3_BUCKET = ""
    response = _response(
        auth_client.post(
            reverse("ingestion-file-import"),
            {"storage_backends": ["s3"], "storage_prefix": "daily"},
            format="json",
        )
    )
    assert response.status_code == 400


def test_import_queues_a_matching_rule_and_keeps_the_upload(
    auth_client: APIClient,
    user: User,
    ingestion_root: Path,
    settings,
) -> None:
    _create_rule(auth_client, _rule_body(name="Daily CSV", prefix="market"))
    payload = b"exchange,symbol,close\nXNYS,ACME,1\n"
    (ingestion_root / "alpha.csv").write_bytes(payload)
    response = _response(
        auth_client.post(
            reverse("ingestion-file-import"),
            {"storage_backends": ["local"], "storage_prefix": "market"},
            format="json",
        )
    )
    assert response.status_code == 201
    stored_path = (
        Path(settings.INGESTION_SYSTEM_ROOT) / "users" / str(user.pk) / "files" / "alpha.csv"
    )
    assert stored_path.is_file()
    assert (ingestion_root / "alpha.csv").read_bytes() == payload
    queued = _data_dict(response)["queued"]
    assert isinstance(queued, list)
    assert len(queued) == 1
    assert (
        Ingestion.objects.filter(
            owner=user, file_name="alpha.csv", state=IngestionState.QUEUED
        ).count()
        == 1
    )
    _import_files(auth_client, ingestion_root, _csv("alpha.csv", payload))
    assert Ingestion.objects.filter(owner=user, file_name="alpha.csv").count() == 1


def test_import_discovers_files_added_after_the_previous_request(
    auth_client: APIClient,
    user: User,
    ingestion_root: Path,
) -> None:
    _create_rule(auth_client)
    first = ingestion_root / "first.csv"
    first.write_bytes(b"exchange,symbol,close\nXNYS,ACME,1\n")
    import_url = reverse("ingestion-file-import")
    request_data = {"storage_backends": ["local"], "storage_prefix": "files"}

    initial = _response(auth_client.post(import_url, request_data, format="json"))
    assert initial.status_code == 201
    assert first.is_file()

    added = ingestion_root / "added.csv"
    added.write_bytes(b"exchange,symbol,close\nXNYS,BETA,2\n")
    refreshed = _response(auth_client.post(import_url, request_data, format="json"))

    assert refreshed.status_code == 201
    queued = _data_dict(refreshed)["queued"]
    assert isinstance(queued, list)
    assert [item["name"] for item in queued if isinstance(item, dict)] == ["added.csv"]
    assert added.is_file()
    assert Ingestion.objects.filter(owner=user).count() == 2


def test_validation_rule_set_endpoint_is_removed() -> None:
    with pytest.raises(Resolver404):
        resolve(f"/{settings.API_PREFIX}ingestion/validation-rule-sets/")


def _log_ingestion(rule: IngestionRule, owner: User, name: str, state: str) -> Ingestion:
    return Ingestion.objects.create(
        rule=rule,
        owner=owner,
        storage_backend=StorageBackend.LOCAL,
        object_key=name,
        file_name=name,
        file_size_bytes=10,
        file_modified_at=timezone.now(),
        content_hash=name.encode().hex()[:64].ljust(64, "a"),
        data_type="DAILY_OHLCV",
        state=state,
        accepted_rows=10,
        rejected_rows=2,
        duplicate_rows=1,
        finished_at=timezone.now() if state != IngestionState.QUEUED else None,
    )


def test_processing_log_reads_ingestion_counters_only(
    auth_client: APIClient,
    other_client: APIClient,
    user: User,
    rule: IngestionRule,
) -> None:
    finished = _log_ingestion(rule, user, "done.csv", IngestionState.SUCCESS)
    _log_ingestion(rule, user, "waiting.csv", IngestionState.QUEUED)

    with CaptureQueriesContext(connection) as captured:
        response = _response(auth_client.get(reverse("ingestion-logs")))

    sql = " ".join(item["sql"] for item in captured.captured_queries)
    assert "mda_trades" not in sql
    assert "mda_daily_ohlcv" not in sql
    assert response.status_code == 200
    results = _data_dict(response)["results"]
    assert isinstance(results, list)
    assert len(results) == 1
    row = results[0]
    assert isinstance(row, dict)
    assert row["id"] == str(finished.pk)
    assert row["accepted_rows"] == 10
    assert row["rejected_rows"] == 2
    assert row["duplicate_rows"] == 1
    hidden = _data_dict(_response(other_client.get(reverse("ingestion-logs"))))
    assert hidden["results"] == []
