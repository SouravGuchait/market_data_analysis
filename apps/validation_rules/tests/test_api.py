"""API tests for ownership, lifecycle transitions, and audit attribution."""

from __future__ import annotations

import uuid

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.audit.models import AuditAction, AuditEvent, AuditResourceType
from apps.validation_rules.models import (
    UserValidationConfiguration,
    ValidationConfigurationStatus,
    ValidationConfigurationVersion,
)


def configuration_url(configuration_id: uuid.UUID | str) -> str:
    return reverse(
        "validation_rules:validation-rule-detail",
        kwargs={"configuration_id": configuration_id},
    )


def compiled_config_url(configuration_id: uuid.UUID | str) -> str:
    return reverse(
        "validation_rules:validation-rule-compiled-config",
        kwargs={"configuration_id": configuration_id},
    )


def approve_url(configuration_id: uuid.UUID | str) -> str:
    return reverse(
        "validation_rules:validation-rule-approve",
        kwargs={"configuration_id": configuration_id},
    )


def reject_url(configuration_id: uuid.UUID | str) -> str:
    return reverse(
        "validation_rules:validation-rule-reject",
        kwargs={"configuration_id": configuration_id},
    )


def draft_payload(asset_type: str = "ohlcv") -> dict[str, str]:
    return {"asset_type": asset_type, "description": "close must be greater than zero"}


def compiled_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "asset_type": "ohlcv",
        "rules": [
            {
                "rule_id": "close_positive",
                "column": "close",
                "operator": "greater_than",
                "value": 0,
                "severity": "error",
                "message": "close must be greater than zero",
            }
        ],
    }


def create_draft(client, asset_type: str = "ohlcv"):
    return client.post(
        reverse("validation_rules:validation-rule-list"), draft_payload(asset_type), format="json"
    )


@pytest.mark.django_db
def test_rule_collection_and_schema_require_authentication(api_client) -> None:
    assert api_client.get(reverse("validation_rules:validation-rule-list")).status_code == 401
    assert (
        api_client.post(
            reverse("validation_rules:validation-rule-list"), {}, format="json"
        ).status_code
        == 401
    )
    schema_url = f"{reverse('validation_rules:validation-rule-schema')}?asset_type=ohlcv"
    assert api_client.get(schema_url).status_code == 401


@pytest.mark.django_db
def test_create_derives_owner_and_records_current_user_audit(
    auth_client,
    user: User,
    other_user: User,
) -> None:
    response = auth_client.post(
        reverse("validation_rules:validation-rule-list"),
        {**draft_payload(), "user": other_user.pk},
        format="json",
    )

    assert response.status_code == 201
    configuration = UserValidationConfiguration.objects.get(asset_type="ohlcv")
    assert configuration.user == user
    data = response.json()["data"]
    assert data["current_version"]["version"] == 1
    assert data["current_version"]["status"] == ValidationConfigurationStatus.DRAFT
    assert "user" not in data
    event = AuditEvent.objects.get(resource_id=str(configuration.pk))
    assert event.actor == user
    assert event.action == AuditAction.VALIDATION_CONFIGURATION_CREATED
    assert event.resource_type == AuditResourceType.VALIDATION_CONFIGURATION


@pytest.mark.django_db
def test_one_current_configuration_per_asset_type_and_owner(
    auth_client,
    other_auth_client,
    user: User,
    other_user: User,
) -> None:
    assert create_draft(auth_client).status_code == 201
    duplicate = create_draft(auth_client)
    assert duplicate.status_code == 409
    assert create_draft(auth_client, "tick").status_code == 201
    assert create_draft(other_auth_client).status_code == 201
    assert UserValidationConfiguration.objects.filter(user=user).count() == 2
    assert UserValidationConfiguration.objects.filter(user=other_user).count() == 1


@pytest.mark.django_db
def test_list_and_detail_are_scoped_and_detail_includes_versions(
    auth_client,
    other_auth_client,
    user: User,
) -> None:
    own_response = create_draft(auth_client)
    own_id = own_response.json()["data"]["id"]
    foreign_response = create_draft(other_auth_client)
    foreign_id = foreign_response.json()["data"]["id"]

    listing = auth_client.get(reverse("validation_rules:validation-rule-list"))
    assert listing.status_code == 200
    assert listing.json()["data"]["count"] == 1
    assert listing.json()["data"]["results"][0]["id"] == own_id

    detail = auth_client.get(configuration_url(own_id))
    assert detail.status_code == 200
    assert len(detail.json()["data"]["versions"]) == 1
    assert (
        auth_client.get(configuration_url(foreign_id)).status_code
        == auth_client.get(configuration_url(uuid.uuid4())).status_code
        == 404
    )
    assert UserValidationConfiguration.objects.filter(user=user).count() == 1


@pytest.mark.django_db
def test_patch_edits_draft_fields_only(auth_client, user: User, other_user: User) -> None:
    created = create_draft(auth_client)
    configuration_id = created.json()["data"]["id"]

    response = auth_client.patch(
        configuration_url(configuration_id),
        {"description": "close must be at least zero", "user": other_user.pk},
        format="json",
    )

    assert response.status_code == 200
    version = UserValidationConfiguration.objects.get(user=user).current_version
    assert version is not None
    assert version.version == 1
    assert version.description == "close must be at least zero"
    assert version.status == ValidationConfigurationStatus.DRAFT
    assert version.compiled_config is None


@pytest.mark.django_db
def test_schema_returns_asset_specific_allowlist(auth_client) -> None:
    ohlcv = auth_client.get(
        f"{reverse('validation_rules:validation-rule-schema')}?asset_type=ohlcv"
    )
    tick = auth_client.get(f"{reverse('validation_rules:validation-rule-schema')}?asset_type=tick")
    invalid = auth_client.get(
        f"{reverse('validation_rules:validation-rule-schema')}?asset_type=options"
    )

    assert ohlcv.status_code == tick.status_code == 200
    assert invalid.status_code == 400
    assert "close" in {column["name"] for column in ohlcv.json()["data"]["columns"]}
    assert "price" in {column["name"] for column in tick.json()["data"]["columns"]}
    assert "close" not in {column["name"] for column in tick.json()["data"]["columns"]}


@pytest.mark.django_db
def test_manual_import_is_staff_only_and_owner_scoped(
    auth_client,
    staff_auth_client,
    user: User,
) -> None:
    created = create_draft(auth_client)
    configuration_id = created.json()["data"]["id"]

    ordinary_response = auth_client.post(
        compiled_config_url(configuration_id),
        compiled_payload(),
        format="json",
    )
    staff_foreign_response = staff_auth_client.post(
        compiled_config_url(configuration_id),
        compiled_payload(),
        format="json",
    )

    assert ordinary_response.status_code == 403
    assert staff_foreign_response.status_code == 404
    current = UserValidationConfiguration.objects.get(user=user).current_version
    assert current is not None
    assert current.status == ValidationConfigurationStatus.DRAFT


@pytest.mark.django_db
def test_staff_owner_can_import_then_owner_can_approve_and_audit(
    staff_auth_client,
    staff_user: User,
) -> None:
    created = create_draft(staff_auth_client)
    configuration_id = created.json()["data"]["id"]

    imported = staff_auth_client.post(
        compiled_config_url(configuration_id),
        compiled_payload(),
        format="json",
    )
    assert imported.status_code == 200
    assert imported.json()["data"]["current_version"]["status"] == (
        ValidationConfigurationStatus.NEEDS_REVIEW
    )

    approved = staff_auth_client.post(approve_url(configuration_id), {}, format="json")
    assert approved.status_code == 200
    approved_version = UserValidationConfiguration.objects.get(user=staff_user).current_version
    assert approved_version is not None
    assert approved_version.status == ValidationConfigurationStatus.APPROVED
    assert approved_version.approved_by == staff_user
    assert approved_version.approved_at is not None
    assert approved_version.compiled_config == compiled_payload()

    audit = staff_auth_client.get("/api/v1/audit/?resource_type=validation_configuration")
    assert audit.status_code == 200
    actions = {item["action"] for item in audit.json()["data"]["results"]}
    assert actions == {
        AuditAction.VALIDATION_CONFIGURATION_CREATED,
        AuditAction.VALIDATION_CONFIG_IMPORTED,
        AuditAction.VALIDATION_CONFIGURATION_APPROVED,
    }


@pytest.mark.django_db
def test_invalid_manual_import_does_not_change_draft(
    staff_auth_client,
    staff_user: User,
) -> None:
    created = create_draft(staff_auth_client)
    configuration_id = created.json()["data"]["id"]
    invalid = compiled_payload()
    rules = invalid["rules"]
    assert isinstance(rules, list)
    first = rules[0]
    assert isinstance(first, dict)
    first["operator"] = "run_python"

    response = staff_auth_client.post(
        compiled_config_url(configuration_id),
        invalid,
        format="json",
    )

    assert response.status_code == 400
    current = UserValidationConfiguration.objects.get(user=staff_user).current_version
    assert current is not None
    assert current.status == ValidationConfigurationStatus.DRAFT
    assert current.compiled_config is None


@pytest.mark.django_db
def test_approval_is_blocked_until_a_valid_import(auth_client) -> None:
    created = create_draft(auth_client)
    configuration_id = created.json()["data"]["id"]

    response = auth_client.post(approve_url(configuration_id), {}, format="json")

    assert response.status_code == 409
    assert response.json()["success"] is False
    assert (
        ValidationConfigurationVersion.objects.get().status == ValidationConfigurationStatus.DRAFT
    )


@pytest.mark.django_db
def test_approved_version_is_immutable_and_patch_creates_a_new_draft(
    staff_auth_client,
    staff_user: User,
) -> None:
    created = create_draft(staff_auth_client)
    configuration_id = created.json()["data"]["id"]
    assert (
        staff_auth_client.post(
            compiled_config_url(configuration_id),
            compiled_payload(),
            format="json",
        ).status_code
        == 200
    )
    assert (
        staff_auth_client.post(approve_url(configuration_id), {}, format="json").status_code == 200
    )
    approved = UserValidationConfiguration.objects.get(user=staff_user).current_version
    assert approved is not None
    approved_id = approved.pk
    approved_description = approved.description
    approved_config = approved.compiled_config
    approved_at = approved.approved_at

    patched = staff_auth_client.patch(
        configuration_url(configuration_id),
        {"description": "new instructions"},
        format="json",
    )

    assert patched.status_code == 200
    configuration = UserValidationConfiguration.objects.get(user=staff_user)
    current = configuration.current_version
    assert current is not None
    assert current.version == 2
    assert current.status == ValidationConfigurationStatus.DRAFT
    assert current.compiled_config is None
    assert current.description == "new instructions"
    approved.refresh_from_db()
    assert approved.pk == approved_id
    assert approved.version == 1
    assert approved.status == ValidationConfigurationStatus.APPROVED
    assert approved.description == approved_description
    assert approved.compiled_config == approved_config
    assert approved.approved_at == approved_at
    detail = staff_auth_client.get(configuration_url(configuration_id))
    assert [row["version"] for row in detail.json()["data"]["versions"]] == [2, 1]


@pytest.mark.django_db
def test_reject_then_edit_returns_current_version_to_draft(staff_auth_client) -> None:
    created = create_draft(staff_auth_client)
    configuration_id = created.json()["data"]["id"]
    staff_auth_client.post(compiled_config_url(configuration_id), compiled_payload(), format="json")

    rejected = staff_auth_client.post(reject_url(configuration_id), {}, format="json")
    assert rejected.status_code == 200
    assert rejected.json()["data"]["current_version"]["status"] == (
        ValidationConfigurationStatus.REJECTED
    )
    edited = staff_auth_client.patch(
        configuration_url(configuration_id),
        {"description": "corrected instructions"},
        format="json",
    )
    assert edited.status_code == 200
    current = edited.json()["data"]["current_version"]
    assert current["status"] == ValidationConfigurationStatus.DRAFT
    assert current["compiled_config"] is None
    assert current["description"] == "corrected instructions"


@pytest.mark.django_db
def test_pending_review_must_be_rejected_before_edit(staff_auth_client) -> None:
    created = create_draft(staff_auth_client)
    configuration_id = created.json()["data"]["id"]
    assert (
        staff_auth_client.post(
            compiled_config_url(configuration_id), compiled_payload(), format="json"
        ).status_code
        == 200
    )

    response = staff_auth_client.patch(
        configuration_url(configuration_id),
        {"description": "another draft"},
        format="json",
    )
    assert response.status_code == 409


@pytest.mark.django_db
def test_configuration_list_uses_bounded_queries(auth_client) -> None:
    for asset_type in ("ohlcv", "tick"):
        assert create_draft(auth_client, asset_type).status_code == 201

    with CaptureQueriesContext(connection) as captured:
        response = auth_client.get(reverse("validation_rules:validation-rule-list"))

    assert response.status_code == 200
    assert response.json()["data"]["count"] == 2
    assert len(captured.captured_queries) == 2


@pytest.mark.django_db
def test_owned_audit_events_are_not_visible_to_another_user(
    auth_client,
    other_auth_client,
    user: User,
    other_user: User,
) -> None:
    create_draft(auth_client)
    create_draft(other_auth_client)

    own_events = auth_client.get("/api/v1/audit/?action=validation_configuration_created")
    foreign_events = other_auth_client.get("/api/v1/audit/?action=validation_configuration_created")
    assert own_events.status_code == foreign_events.status_code == 200
    assert own_events.json()["data"]["count"] == 1
    assert foreign_events.json()["data"]["count"] == 1
    assert AuditEvent.objects.filter(actor=user).count() == 1
    assert AuditEvent.objects.filter(actor=other_user).count() == 1
