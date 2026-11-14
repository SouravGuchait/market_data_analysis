"""Authenticated file upload, rules, manual scan and dispatch."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework.response import Response
from rest_framework.test import APIClient

from apps.audit.models import AuditAction, AuditEvent
from apps.ingestion.models import Ingestion, IngestionRule, IngestionState, StorageFile
from apps.ingestion.s3_source import RemoteObject
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
    for upload in files:
        staged = root / Path(upload.name or "upload.bin").name
        if staged.is_file():
            staged.unlink()
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


def _scan(client: APIClient, rule_id: str) -> dict[str, object]:
    response = _response(
        client.post(reverse("ingestion-scan"), {"rule_id": rule_id}, format="json")
    )
    assert response.status_code == 200, response.content
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
        assert key.startswith(f"users/{user.pk}/market/")
        assert settings.INGESTION_LOCAL_ROOT not in key
        assert (ingestion_root / key).is_file()
        assert not (ingestion_root / "alpha.csv").exists()
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
    auth_client: APIClient, other_client: APIClient, user: User, ingestion_root
) -> None:
    created = _create_rule(auth_client)
    assert created["storage_prefix"] == "market"
    assert "location_reference" not in created
    assert (ingestion_root / "users" / str(user.pk) / "market").is_dir()

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


def test_rule_create_builds_a_missing_ingestion_root(
    auth_client: APIClient, ingestion_root: Path
) -> None:
    ingestion_root.rmdir()

    created = _response(
        auth_client.post(reverse("ingestion-rule-list"), _rule_body(), format="json")
    )

    assert created.status_code == 201
    assert ingestion_root.is_dir()


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


def test_manual_scan_defers_unstable_files_and_queues_them_on_the_next_scan(
    auth_client: APIClient,
    ingestion_root,
) -> None:
    _import_files(auth_client, ingestion_root, _csv("synthetic_market.csv"))
    rule = _create_rule(auth_client)

    first = _scan(auth_client, _text_field(rule, "id"))
    assert first["discovered_count"] == 1
    assert first["queued_count"] == 0
    assert first["skipped_count"] == 1
    assert Ingestion.objects.count() == 0

    second = _scan(auth_client, _text_field(rule, "id"))
    assert second["queued_count"] == 1
    ingestion = Ingestion.objects.get()
    assert ingestion.state == IngestionState.QUEUED
    assert ingestion.celery_task_id == ""
    assert ingestion.file_name == "synthetic_market.csv"
    assert ingestion.owner == IngestionRule.objects.get().owner


def test_many_files_receive_separate_ingestion_ids(
    auth_client: APIClient,
    ingestion_root,
) -> None:
    files = [
        _csv(f"market-{index:02}.csv", f"symbol,close\nA,{index}\n".encode()) for index in range(11)
    ]
    _import_files(auth_client, ingestion_root, *files)
    rule = _create_rule(auth_client)
    _scan(auth_client, _text_field(rule, "id"))
    second = _scan(auth_client, _text_field(rule, "id"))

    assert second["discovered_count"] == 11
    assert second["queued_count"] == 11
    assert Ingestion.objects.count() == 11
    assert Ingestion.objects.values("id").distinct().count() == 11


def test_dispatch_waits_for_commit_and_does_not_repeat(
    auth_client: APIClient,
    ingestion_root,
    django_capture_on_commit_callbacks,
) -> None:
    _import_files(auth_client, ingestion_root, _csv("one.csv"))
    rule = _create_rule(auth_client)
    _scan(auth_client, _text_field(rule, "id"))
    _scan(auth_client, _text_field(rule, "id"))
    ingestion = Ingestion.objects.get()

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        held = _response(auth_client.post(reverse("ingestion-dispatch"), {}, format="json"))

    assert held.status_code == 200
    assert _data_dict(held)["queued"] == 1
    ingestion.refresh_from_db()
    assert ingestion.celery_task_id == ""
    assert callbacks

    dispatched: list[str] = []

    def delay(ingestion_id: str) -> _DelayedTask:
        dispatched.append(ingestion_id)
        return _DelayedTask("task-1")

    with (
        patch("apps.ingestion.tasks.process_ingestion_file.delay", side_effect=delay),
        django_capture_on_commit_callbacks(execute=True),
    ):
        for callback in callbacks:
            callback()

    ingestion.refresh_from_db()
    assert dispatched == [str(ingestion.pk)]
    assert ingestion.celery_task_id == "task-1"

    with (
        patch("apps.ingestion.tasks.process_ingestion_file.delay", side_effect=delay),
        django_capture_on_commit_callbacks(execute=True),
    ):
        repeated = _response(auth_client.post(reverse("ingestion-dispatch"), {}, format="json"))

    repeated_data = _data_dict(repeated)
    assert repeated_data["queued"] == 0
    assert repeated_data["skipped"] == 1
    assert dispatched == [str(ingestion.pk)]


def test_scan_and_dispatch_accept_the_names_the_user_created(
    auth_client: APIClient,
    ingestion_root,
    django_capture_on_commit_callbacks,
) -> None:
    _import_files(auth_client, ingestion_root, _csv("one.csv"))
    _create_rule(auth_client, _rule_body("Daily candles"))
    first = _scan(auth_client, "Daily candles")
    assert first["queued_count"] == 0
    assert first["rule_name"] == "Daily candles"
    assert _scan(auth_client, "Daily candles")["queued_count"] == 1

    with (
        patch(
            "apps.ingestion.tasks.process_ingestion_file.delay",
            return_value=_DelayedTask("named-1"),
        ),
        django_capture_on_commit_callbacks(execute=True),
    ):
        response = _response(
            auth_client.post(
                reverse("ingestion-dispatch"),
                {"ingestion_ids": ["one.csv"]},
                format="json",
            )
        )

    assert response.status_code == 200
    references = _data_dict(response)["ingestion_ids"]
    assert isinstance(references, list)
    named = references[0]
    assert isinstance(named, dict)
    assert named.get("name") == "one.csv"
    assert named.get("id") == str(Ingestion.objects.get().pk)


def test_duplicate_names_are_rejected(
    auth_client: APIClient,
    ingestion_root,
) -> None:
    _create_rule(auth_client, _rule_body("Shared", "market"))
    _create_rule(auth_client, _rule_body("Shared", "ticks"))
    scan = _response(
        auth_client.post(reverse("ingestion-scan"), {"rule_id": "Shared"}, format="json")
    )
    assert scan.status_code == 400

    _import_files(
        auth_client,
        ingestion_root,
        _csv("one.csv", b"symbol,close\nA,1\n"),
        prefix="market",
    )
    _import_files(
        auth_client,
        ingestion_root,
        _csv("one.csv", b"symbol,close\nB,2\n"),
        prefix="other",
    )
    other = _create_rule(auth_client, _rule_body("Unique other", "other"))
    rule = _create_rule(auth_client, _rule_body("Unique daily"))
    for rule_id in (_text_field(other, "id"), _text_field(rule, "id")):
        _scan(auth_client, rule_id)
        _scan(auth_client, rule_id)
    dispatch = _response(
        auth_client.post(
            reverse("ingestion-dispatch"),
            {"ingestion_ids": ["one.csv"]},
            format="json",
        )
    )
    assert dispatch.status_code == 400


def test_filters_pagination_and_cross_user_detail(
    auth_client: APIClient,
    other_client: APIClient,
    ingestion_root,
) -> None:
    _import_files(auth_client, ingestion_root, _csv("daily.csv"), prefix="market")
    _import_files(
        auth_client, ingestion_root, _csv("ticks.csv", b"price,qty\n1,2\n"), prefix="ticks"
    )
    daily = _create_rule(auth_client, _rule_body("Daily", "market"))
    tick_body = _rule_body("Ticks", "ticks")
    tick_body["data_type"] = "TICK_DATA"
    ticks = _create_rule(auth_client, tick_body)
    for rule_id in (_text_field(daily, "id"), _text_field(ticks, "id")):
        _scan(auth_client, rule_id)
        _scan(auth_client, rule_id)

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

    foreign_dispatch = _response(
        other_client.post(
            reverse("ingestion-dispatch"),
            {"ingestion_ids": [ingestion_id]},
            format="json",
        )
    )
    assert foreign_dispatch.status_code == 200
    assert _data_dict(foreign_dispatch)["queued"] == 0


def test_retry_is_limited_to_the_owner_failed_ingestion(
    auth_client: APIClient,
    ingestion_root,
    django_capture_on_commit_callbacks,
) -> None:
    _import_files(auth_client, ingestion_root, _csv("retry.csv"))
    rule = _create_rule(auth_client)
    _scan(auth_client, _text_field(rule, "id"))
    _scan(auth_client, _text_field(rule, "id"))
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


def test_duplicate_scan_does_not_create_a_second_ingestion(
    auth_client: APIClient,
    ingestion_root,
) -> None:
    _import_files(auth_client, ingestion_root, _csv("once.csv"))
    rule = _create_rule(auth_client)
    _scan(auth_client, _text_field(rule, "id"))
    _scan(auth_client, _text_field(rule, "id"))
    third = _scan(auth_client, _text_field(rule, "id"))

    assert third["queued_count"] == 0
    assert third["skipped_count"] == 1
    assert Ingestion.objects.count() == 1


def test_disabled_rule_and_foreign_rule_are_rejected(
    auth_client: APIClient,
    other_client: APIClient,
    ingestion_root,
) -> None:
    rule = _create_rule(auth_client)
    IngestionRule.objects.filter(pk=_text_field(rule, "id")).update(is_enabled=False)
    disabled = _response(
        auth_client.post(
            reverse("ingestion-scan"),
            {"rule_id": _text_field(rule, "id")},
            format="json",
        )
    )
    assert disabled.status_code == 400

    missing = _response(
        other_client.post(
            reverse("ingestion-scan"),
            {"rule_id": _text_field(rule, "id")},
            format="json",
        )
    )
    assert missing.status_code == 404
    bare = _response(
        auth_client.post(reverse("ingestion-scan"), {"path": "/tmp/data"}, format="json")
    )
    assert bare.status_code == 400


def test_import_copies_every_configured_local_file(
    auth_client: APIClient,
    user: User,
    ingestion_root,
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
    copied = ingestion_root / "users" / str(user.pk) / "daily" / "synthetic_market_clean.csv"
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
    stored = _data_dict(response)["stored"]
    assert isinstance(stored, list)
    first = stored[0]
    assert isinstance(first, dict)
    assert first.get("original_filename") == "synthetic_ticks.csv"
    assert StorageFile.objects.filter(original_filename="synthetic_ticks.csv").count() == 1


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
