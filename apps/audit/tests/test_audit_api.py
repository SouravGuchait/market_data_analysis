"""API and service tests for append-only audit records."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIRequestFactory

from apps.audit.models import AuditAction, AuditEvent, AuditResourceType, AuditStatus
from apps.audit.services import record_audit_event, record_system_event

AUDIT_URL = "/api/v1/audit/"


def detail_url(audit_id) -> str:
    return f"{AUDIT_URL}{audit_id}/"


def make_event(
    *,
    actor=None,
    action: str = AuditAction.SYSTEM_EVENT,
    resource_type: str = AuditResourceType.SYSTEM,
    resource_id: str = "",
    status: str = AuditStatus.SUCCESS,
    metadata: dict | None = None,
) -> AuditEvent:
    return record_audit_event(
        actor=actor,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        status=status,
        metadata=metadata,
    )


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["get", "post", "delete"])
def test_collection_rejects_unsupported_or_unauthenticated_methods(api_client, method):
    response = getattr(api_client, method)(AUDIT_URL)

    assert response.status_code == 401


@pytest.mark.django_db
def test_detail_requires_authentication(api_client, user):
    event = make_event(actor=user, action=AuditAction.LOGIN_SUCCESS, resource_type="user")

    response = api_client.get(detail_url(event.pk))

    assert response.status_code == 401


@pytest.mark.django_db
def test_user_sees_only_their_own_events(auth_client, user, other_user):
    own = make_event(
        actor=user,
        action=AuditAction.LOGIN_SUCCESS,
        resource_type=AuditResourceType.USER,
        resource_id=user.pk,
    )
    make_event(
        actor=other_user,
        action=AuditAction.LOGIN_SUCCESS,
        resource_type=AuditResourceType.USER,
        resource_id=other_user.pk,
    )
    make_event(action=AuditAction.INGESTION_STARTED, resource_type=AuditResourceType.INGESTION)

    response = auth_client.get(AUDIT_URL)

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["errors"] is None
    assert payload["data"]["count"] == 1
    assert payload["data"]["results"][0]["id"] == str(own.pk)
    assert payload["data"]["results"][0]["actor"] == {"id": str(user.pk), "username": user.username}


@pytest.mark.django_db
def test_staff_user_can_inspect_all_events(staff_auth_client, user, other_user):
    make_event(actor=user, action=AuditAction.LOGIN_SUCCESS, resource_type="user")
    make_event(actor=other_user, action=AuditAction.LOGOUT, resource_type="user")
    make_event(action=AuditAction.INGESTION_FAILED, resource_type="ingestion")

    response = staff_auth_client.get(AUDIT_URL)

    assert response.status_code == 200
    assert response.json()["data"]["count"] == 3


@pytest.mark.django_db
def test_foreign_and_missing_detail_return_the_same_not_found_response(other_auth_client, user):
    own_event = make_event(actor=user, action=AuditAction.REGISTRATION, resource_type="user")

    foreign_response = other_auth_client.get(detail_url(own_event.pk))
    missing_response = other_auth_client.get(detail_url(uuid.uuid4()))

    assert foreign_response.status_code == 404
    assert missing_response.status_code == 404
    assert (
        foreign_response.json()
        == missing_response.json()
        == {
            "success": False,
            "message": "Audit event not found",
            "data": None,
            "errors": {"detail": ["Not found."]},
        }
    )


@pytest.mark.django_db
def test_detail_returns_the_owned_event(auth_client, user):
    event = make_event(
        actor=user,
        action=AuditAction.PORTFOLIO_CREATED,
        resource_type=AuditResourceType.PORTFOLIO,
        resource_id="portfolio-1",
        metadata={"name": "Retirement"},
    )

    response = auth_client.get(detail_url(event.pk))

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["id"] == str(event.pk)
    assert data["action"] == AuditAction.PORTFOLIO_CREATED
    assert data["resource_id"] == "portfolio-1"
    assert data["metadata"] == {"name": "Retirement"}
    assert data["actor"]["username"] == user.username


@pytest.mark.django_db
def test_filters_by_action_resource_type_status_and_date(auth_client, user):
    old = make_event(
        actor=user,
        action=AuditAction.LOGIN_SUCCESS,
        resource_type=AuditResourceType.USER,
        status=AuditStatus.SUCCESS,
    )
    AuditEvent.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=3))
    make_event(
        actor=user,
        action=AuditAction.LOGIN_FAILED,
        resource_type=AuditResourceType.USER,
        status=AuditStatus.FAILURE,
    )
    make_event(
        actor=user,
        action=AuditAction.PORTFOLIO_CREATED,
        resource_type=AuditResourceType.PORTFOLIO,
        status=AuditStatus.SUCCESS,
    )

    action_response = auth_client.get(f"{AUDIT_URL}?action=login_failed")
    resource_response = auth_client.get(f"{AUDIT_URL}?resource_type=portfolio")
    status_response = auth_client.get(f"{AUDIT_URL}?status=failure")
    date_response = auth_client.get(
        f"{AUDIT_URL}?start_date={timezone.localdate().isoformat()}"
        f"&end_date={timezone.localdate().isoformat()}"
    )

    assert action_response.json()["data"]["count"] == 1
    assert action_response.json()["data"]["results"][0]["action"] == "login_failed"
    assert resource_response.json()["data"]["count"] == 1
    assert resource_response.json()["data"]["results"][0]["resource_type"] == "portfolio"
    assert status_response.json()["data"]["count"] == 1
    assert status_response.json()["data"]["results"][0]["status"] == "failure"
    assert date_response.json()["data"]["count"] == 2


@pytest.mark.django_db
def test_filter_validation_errors(auth_client):
    invalid_status = auth_client.get(f"{AUDIT_URL}?status=unknown")
    invalid_date = auth_client.get(f"{AUDIT_URL}?start_date=not-a-date")
    inverted_range = auth_client.get(f"{AUDIT_URL}?start_date=2026-11-10&end_date=2026-11-09")

    assert invalid_status.status_code == 400
    assert "status" in invalid_status.json()["errors"]
    assert invalid_date.status_code == 400
    assert "start_date" in invalid_date.json()["errors"]
    assert inverted_range.status_code == 400
    assert "end_date" in inverted_range.json()["errors"]


@pytest.mark.django_db
def test_collection_is_paginated(auth_client, user):
    for index in range(21):
        make_event(
            actor=user,
            action=AuditAction.PORTFOLIO_UPDATED,
            resource_type=AuditResourceType.PORTFOLIO,
            resource_id=str(index),
        )

    first_page = auth_client.get(AUDIT_URL)
    second_page = auth_client.get(f"{AUDIT_URL}?page=2")

    assert first_page.status_code == 200
    assert first_page.json()["data"]["count"] == 21
    assert len(first_page.json()["data"]["results"]) == 20
    assert first_page.json()["data"]["next"] is not None
    assert len(second_page.json()["data"]["results"]) == 1


@pytest.mark.django_db
def test_collection_avoids_n_plus_one_actor_queries(auth_client, user):
    for _index in range(3):
        make_event(actor=user, action=AuditAction.LOGIN_SUCCESS, resource_type="user")

    with CaptureQueriesContext(connection) as captured:
        response = auth_client.get(AUDIT_URL)

    assert response.status_code == 200
    # pagination count + page query with select_related("actor")
    assert len(captured.captured_queries) == 2


@pytest.mark.django_db
def test_service_records_request_correlation_and_source_ip(user):
    request = APIRequestFactory().post("/api/v1/auth/login/", REMOTE_ADDR="203.0.113.10")
    request.__dict__["request_id"] = "request-123"

    event = record_audit_event(
        actor=user,
        action=AuditAction.LOGIN_SUCCESS,
        resource_type=AuditResourceType.USER,
        resource_id=user.pk,
        status=AuditStatus.SUCCESS,
        request=request,
    )

    assert event.request_id == "request-123"
    assert event.source_ip == "203.0.113.10"


@pytest.mark.django_db
def test_service_does_not_trust_forwarding_headers(user):
    request = APIRequestFactory().post(
        "/api/v1/auth/login/",
        REMOTE_ADDR="198.51.100.4",
        HTTP_X_FORWARDED_FOR="203.0.113.99",
    )

    event = record_audit_event(
        actor=user,
        action=AuditAction.LOGIN_SUCCESS,
        resource_type=AuditResourceType.USER,
        status=AuditStatus.SUCCESS,
        request=request,
    )

    assert event.source_ip == "198.51.100.4"


@pytest.mark.django_db
def test_service_redacts_sensitive_metadata(user):
    event = record_audit_event(
        actor=user,
        action=AuditAction.LOGIN_SUCCESS,
        resource_type=AuditResourceType.USER,
        status=AuditStatus.SUCCESS,
        metadata={
            "username": "audit-user",
            "password": "do-not-store",
            "nested": {"refresh_token": "do-not-store", "safe": "kept"},
            "items": [{"api_key": "do-not-store"}, "kept"],
        },
    )

    assert event.metadata["username"] == "audit-user"
    assert event.metadata["password"] == "[REDACTED]"
    assert event.metadata["nested"]["refresh_token"] == "[REDACTED]"
    assert event.metadata["nested"]["safe"] == "kept"
    assert event.metadata["items"][0]["api_key"] == "[REDACTED]"
    assert event.metadata["items"][1] == "kept"


@pytest.mark.django_db
def test_system_event_has_no_actor_and_is_marked_as_system():
    event = record_system_event(
        action=AuditAction.INGESTION_STARTED,
        resource_type=AuditResourceType.INGESTION,
        resource_id="ingestion-1",
        metadata={"ingestion_uuid": "ingestion-1"},
    )

    assert event.actor is None
    assert event.metadata == {"ingestion_uuid": "ingestion-1", "system": True}


@pytest.mark.django_db
def test_events_are_append_only(user):
    event = make_event(actor=user, action=AuditAction.LOGIN_SUCCESS, resource_type="user")

    event.action = AuditAction.LOGOUT
    with pytest.raises(ValidationError, match="append-only"):
        event.save()

    with pytest.raises(ValidationError, match="cannot be deleted"):
        event.delete()


@pytest.mark.django_db
def test_deleting_an_actor_preserves_the_event_and_clears_attribution(user):
    event = make_event(actor=user, action=AuditAction.LOGIN_SUCCESS, resource_type="user")

    user.delete()
    event.refresh_from_db()

    assert event.actor is None
    assert AuditEvent.objects.filter(pk=event.pk).exists()
