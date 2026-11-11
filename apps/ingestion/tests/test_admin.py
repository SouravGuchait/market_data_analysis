"""Administrator rule changes are attributed in the audit trail."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, cast

import pytest
from django.contrib import admin
from django.contrib.auth.models import User
from django.test import RequestFactory
from django.utils import timezone

from apps.audit.models import AuditAction, AuditEvent
from apps.ingestion.models import IngestionRule, StorageBackend


@pytest.fixture
def staff_user(db) -> User:
    return User.objects.create_user(
        username="ingestion-admin",
        email="ingestion-admin@example.com",
        password="Ingestion!Admin_Password_9274",
        is_staff=True,
    )


@pytest.mark.django_db
def test_admin_rule_changes_are_audited_for_the_authenticated_user(staff_user) -> None:
    request = RequestFactory().post("/admin/ingestion/ingestionrule/add/")
    request.user = staff_user
    cast(Any, request).request_id = "admin-request-id"

    rule = IngestionRule(
        name="admin-rule",
        storage_backend=StorageBackend.LOCAL,
        location_reference="inbox",
        file_pattern="*.csv",
        supported_formats=["csv"],
        scan_interval_seconds=60,
        stability_policy={"required_stable_scans": 2},
        initial_watermark=timezone.now() - timedelta(days=1),
    )

    model_admin = admin.site._registry[IngestionRule]
    model_admin.save_model(request, rule, None, False)  # type: ignore[arg-type]

    created_event = AuditEvent.objects.get(
        action=AuditAction.INGESTION_RULE_CHANGED,
        resource_id=str(rule.pk),
    )
    assert created_event.actor == staff_user
    assert created_event.metadata["change"] == "created"
    assert created_event.request_id == "admin-request-id"

    rule_id = rule.pk
    model_admin.delete_model(request, rule)  # type: ignore[arg-type]

    events = AuditEvent.objects.filter(
        action=AuditAction.INGESTION_RULE_CHANGED,
        resource_id=str(rule_id),
    ).order_by("created_at")
    assert [event.metadata["change"] for event in events] == ["created", "deleted"]
    assert all(event.actor == staff_user for event in events)
