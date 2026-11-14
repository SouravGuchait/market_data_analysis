"""Owner-scoped reads for ingestion files, rules and records."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime

from django.contrib.auth.models import User
from django.db.models import QuerySet

from .models import Ingestion, IngestionRule, RejectedRow, StorageFile


def list_storage_files(owner: User) -> QuerySet[StorageFile]:
    return StorageFile.objects.filter(owner=owner, is_active=True)


def list_owned_rules(owner: User) -> QuerySet[IngestionRule]:
    return IngestionRule.objects.filter(owner=owner)


def get_owned_rule(owner: User, rule_id: uuid.UUID) -> IngestionRule | None:
    return list_owned_rules(owner).filter(pk=rule_id).first()


def list_owned_ingestions(owner: User) -> QuerySet[Ingestion]:
    return Ingestion.objects.filter(owner=owner).select_related("rule", "storage_file")


def get_owned_ingestion(owner: User, ingestion_id: uuid.UUID) -> Ingestion | None:
    return list_owned_ingestions(owner).filter(pk=ingestion_id).first()


def list_owned_rejections(owner: User, ingestion_id: uuid.UUID) -> QuerySet[RejectedRow]:
    return RejectedRow.objects.filter(
        ingestion_id=ingestion_id,
        ingestion__owner=owner,
    ).select_related("ingestion")


def filter_owned_ingestions(
    queryset: QuerySet[Ingestion],
    filters: Mapping[str, object],
) -> QuerySet[Ingestion]:
    """Apply validated list filters to an already owner-scoped queryset."""

    status = filters.get("status")
    data_type = filters.get("data_type")
    rule_id = filters.get("rule_id")
    filename = filters.get("filename")
    start_date = filters.get("start_date")
    end_date = filters.get("end_date")
    if isinstance(status, str) and status:
        queryset = queryset.filter(state=status)
    if isinstance(data_type, str) and data_type:
        queryset = queryset.filter(data_type=data_type)
    if isinstance(rule_id, uuid.UUID):
        queryset = queryset.filter(rule_id=rule_id)
    if isinstance(filename, str) and filename:
        queryset = queryset.filter(file_name__icontains=filename)
    if isinstance(start_date, datetime):
        queryset = queryset.filter(created_at__gte=start_date)
    if isinstance(end_date, datetime):
        queryset = queryset.filter(created_at__lte=end_date)
    return queryset
