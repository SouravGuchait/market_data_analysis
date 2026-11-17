"""Append-only user and system audit records."""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.base import ModelBase


class AuditAction(models.TextChoices):
    """Attributable actions recorded by the application."""

    LOGIN_SUCCESS = "login_success", "Login succeeded"
    LOGIN_FAILED = "login_failed", "Login failed"
    LOGOUT = "logout", "Logout"
    REGISTRATION = "registration", "Registration"
    PASSWORD_RESET_REQUESTED = "password_reset_requested", "Password reset requested"
    PASSWORD_RESET_COMPLETED = "password_reset_completed", "Password reset completed"
    PORTFOLIO_CREATED = "portfolio_created", "Portfolio created"
    PORTFOLIO_UPDATED = "portfolio_updated", "Portfolio updated"
    PORTFOLIO_DELETED = "portfolio_deleted", "Portfolio deleted"
    PORTFOLIO_INSTRUMENT_ADDED = "portfolio_instrument_added", "Portfolio instrument added"
    PORTFOLIO_INSTRUMENT_REMOVED = "portfolio_instrument_removed", "Portfolio instrument removed"
    INSTRUMENT_CREATED = "instrument_created", "Instrument created"
    INGESTION_RULE_CHANGED = "ingestion_rule_changed", "Ingestion rule changed"
    VALIDATION_CONFIGURATION_CREATED = (
        "validation_configuration_created",
        "Validation configuration created",
    )
    VALIDATION_CONFIGURATION_UPDATED = (
        "validation_configuration_updated",
        "Validation configuration updated",
    )
    VALIDATION_CONFIG_IMPORTED = (
        "validation_config_imported",
        "Validation configuration imported",
    )
    VALIDATION_CONFIGURATION_APPROVED = (
        "validation_configuration_approved",
        "Validation configuration approved",
    )
    VALIDATION_CONFIGURATION_REJECTED = (
        "validation_configuration_rejected",
        "Validation configuration rejected",
    )
    INGESTION_STARTED = "ingestion_started", "Ingestion started"
    INGESTION_COMPLETED = "ingestion_completed", "Ingestion completed"
    INGESTION_FAILED = "ingestion_failed", "Ingestion failed"
    ANALYTICS_EXPORTED = "analytics_exported", "Analytics exported"
    AGGREGATION_TRIGGERED = "aggregation_triggered", "Aggregation triggered"
    AGGREGATION_COMPLETED = "aggregation_completed", "Aggregation completed"
    AGGREGATION_FAILED = "aggregation_failed", "Aggregation failed"
    SYSTEM_EVENT = "system_event", "System event"


class AuditResourceType(models.TextChoices):
    """Resource categories referenced by audit records."""

    USER = "user", "User"
    PORTFOLIO = "portfolio", "Portfolio"
    PORTFOLIO_INSTRUMENT = "portfolio_instrument", "Portfolio instrument"
    INSTRUMENT = "instrument", "Instrument"
    INGESTION = "ingestion", "Ingestion"
    VALIDATION_CONFIGURATION = "validation_configuration", "Validation configuration"
    ANALYTICS = "analytics", "Analytics"
    SYSTEM = "system", "System"


class AuditStatus(models.TextChoices):
    """Outcome of the recorded action."""

    SUCCESS = "success", "Success"
    FAILURE = "failure", "Failure"
    DENIED = "denied", "Denied"
    ACCEPTED = "accepted", "Accepted"
    PARTIAL = "partial", "Partial"


class AuditEvent(models.Model):
    """One immutable audit record.

    Application code creates records through :func:`apps.audit.services.record_audit_event`.
    The model itself rejects updates and deletions so ordinary user-facing code
    cannot rewrite history.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_events",
        help_text="Null for system events and unauthenticated failures.",
    )
    action = models.CharField(max_length=64, choices=AuditAction.choices)
    resource_type = models.CharField(
        max_length=64,
        choices=AuditResourceType.choices,
        blank=True,
        default="",
    )
    resource_id = models.CharField(max_length=128, blank=True, default="")
    status = models.CharField(max_length=20, choices=AuditStatus.choices)
    request_id = models.CharField(max_length=64, blank=True, default="")
    source_ip = models.GenericIPAddressField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "mda_audit_events"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["actor", "-created_at"], name="idx_audit_actor_time"),
            models.Index(fields=["action", "-created_at"], name="idx_audit_action_time"),
            models.Index(
                fields=["resource_type", "resource_id", "-created_at"],
                name="idx_audit_resource_time",
            ),
            models.Index(fields=["status", "-created_at"], name="idx_audit_status_time"),
            models.Index(fields=["-created_at"], name="idx_audit_created_at"),
        ]

    def __str__(self) -> str:
        return f"{self.action} {self.resource_type}:{self.resource_id}".strip()

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            raise ValidationError("Audit events are append-only and cannot be updated.")
        super().save(
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )

    def delete(
        self, using: str | None = None, keep_parents: bool = False
    ) -> tuple[int, dict[str, int]]:
        raise ValidationError("Audit events cannot be deleted through application code.")
