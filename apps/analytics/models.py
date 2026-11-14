"""Persisted analytics aggregation run tracking."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class AggregationTrigger(models.TextChoices):
    """How an aggregation run was requested."""

    INGESTION = "ingestion", "Ingestion"
    MANUAL = "manual", "Manual"
    BACKFILL = "backfill", "Backfill"


class AggregationRunStatus(models.TextChoices):
    """Lifecycle state of one aggregation run."""

    PENDING = "pending", "Pending"
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    DISPATCH_FAILED = "dispatch_failed", "Dispatch failed"


class AggregationRun(models.Model):
    """Durable record of one candle aggregation request and its outcome."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(
        max_length=32,
        choices=AggregationRunStatus.choices,
        default=AggregationRunStatus.PENDING,
    )
    trigger = models.CharField(max_length=16, choices=AggregationTrigger.choices)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="aggregation_runs",
    )
    ingestion = models.ForeignKey(
        "ingestion.Ingestion",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="aggregation_runs",
    )
    instrument_ids = models.JSONField(default=list, blank=True)
    exchange_ids = models.JSONField(default=list, blank=True)
    timeframes = models.JSONField(default=list, blank=True)
    range_start = models.DateTimeField()
    range_end = models.DateTimeField()
    celery_task_id = models.CharField(max_length=255, blank=True, default="")
    candles_created = models.PositiveIntegerField(default=0)
    candles_updated = models.PositiveIntegerField(default=0)
    candles_unchanged = models.PositiveIntegerField(default=0)
    error_summary = models.TextField(blank=True, default="")
    dispatch_attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "mda_aggregation_runs"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["status", "-created_at"], name="idx_agg_run_status_time"),
            models.Index(fields=["trigger", "-created_at"], name="idx_agg_run_trigger_time"),
            models.Index(fields=["ingestion"], name="idx_agg_run_ingestion"),
        ]

    def __str__(self) -> str:
        return f"AggregationRun {self.pk} ({self.status})"
