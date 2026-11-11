"""Administrator-only configuration and inspection for ingestion.

Rules point at server-side storage locations. Ordinary API clients never submit
filesystem paths, and ingestion records are read-only in the admin.
"""

from __future__ import annotations

from django.contrib import admin
from django.db.models import Model
from django.forms import ModelForm
from django.http import HttpRequest

from apps.audit.models import AuditAction, AuditResourceType, AuditStatus
from apps.audit.services import record_audit_event

from .models import (
    Ingestion,
    IngestionObjectState,
    IngestionRule,
    IngestionStateTransition,
)


@admin.register(IngestionRule)
class IngestionRuleAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "storage_backend",
        "location_reference",
        "file_pattern",
        "is_enabled",
        "scan_interval_seconds",
        "last_scan_status",
        "last_scan_completed_at",
    )
    list_filter = ("is_enabled", "storage_backend", "last_scan_status")
    search_fields = ("name", "location_reference", "file_pattern")
    readonly_fields = (
        "last_scan_watermark",
        "last_scan_started_at",
        "last_scan_completed_at",
        "last_scan_status",
        "last_scan_error",
        "last_scan_summary",
        "created_at",
        "updated_at",
    )

    def save_model(
        self,
        request: HttpRequest,
        obj: IngestionRule,
        form: ModelForm[IngestionRule],
        change: bool,
    ) -> None:
        super().save_model(request, obj, form, change)
        record_audit_event(
            actor=request.user,
            action=AuditAction.INGESTION_RULE_CHANGED,
            resource_type=AuditResourceType.INGESTION,
            resource_id=obj.pk,
            status=AuditStatus.SUCCESS,
            request=request,
            metadata={
                "change": "updated" if change else "created",
                "rule_name": obj.name,
                "is_enabled": obj.is_enabled,
                "storage_backend": obj.storage_backend,
            },
        )

    def delete_model(self, request: HttpRequest, obj: IngestionRule) -> None:
        rule_id = obj.pk
        rule_name = obj.name
        super().delete_model(request, obj)
        record_audit_event(
            actor=request.user,
            action=AuditAction.INGESTION_RULE_CHANGED,
            resource_type=AuditResourceType.INGESTION,
            resource_id=rule_id,
            status=AuditStatus.SUCCESS,
            request=request,
            metadata={
                "change": "deleted",
                "rule_name": rule_name,
            },
        )


@admin.register(Ingestion)
class IngestionAdmin(admin.ModelAdmin):
    list_display = (
        "file_name",
        "rule",
        "state",
        "content_hash",
        "celery_task_id",
        "created_at",
    )
    list_filter = ("state", "storage_backend")
    search_fields = ("file_name", "object_key", "content_hash", "celery_task_id")
    readonly_fields = tuple(field.name for field in Ingestion._meta.fields)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Model | None = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Model | None = None) -> bool:
        return False


@admin.register(IngestionStateTransition)
class IngestionStateTransitionAdmin(admin.ModelAdmin):
    list_display = ("ingestion", "from_state", "to_state", "reason", "created_at")
    list_filter = ("to_state",)
    search_fields = ("ingestion__file_name", "reason")
    readonly_fields = tuple(field.name for field in IngestionStateTransition._meta.fields)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Model | None = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Model | None = None) -> bool:
        return False


@admin.register(IngestionObjectState)
class IngestionObjectStateAdmin(admin.ModelAdmin):
    list_display = (
        "object_key",
        "rule",
        "observed_size_bytes",
        "stable_scan_count",
        "last_processed_modified_at",
        "last_seen_at",
    )
    list_filter = ("rule",)
    search_fields = ("object_key", "object_name", "last_content_hash")
    readonly_fields = tuple(field.name for field in IngestionObjectState._meta.fields)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Model | None = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Model | None = None) -> bool:
        return False
