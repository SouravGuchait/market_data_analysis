"""Ingestion rules, discovered object state and ingestion lifecycle records.

Discovery is scheduler-driven. The models here persist the configuration and
the evidence required to avoid processing the same content twice: the previous
scan watermark, per-object metadata across scans, and the content hash of each
ingestion.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

SUPPORTED_FILE_FORMATS: tuple[str, ...] = ("csv", "json", "xlsx")
DEFAULT_REQUIRED_STABLE_SCANS = 2


class IngestionConfigurationError(ValueError):
    """Raised when a rule stores an unusable discovery configuration."""


@dataclass(frozen=True)
class StabilityPolicy:
    """Validated stability and overlap settings for one rule."""

    required_stable_scans: int = DEFAULT_REQUIRED_STABLE_SCANS
    min_file_age_seconds: int = 0
    overlap_seconds: int = 0


def _non_negative_int(policy: dict[str, Any], key: str, default: int) -> int:
    raw = policy.get(key, default)
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise IngestionConfigurationError(
            f"Stability policy field {key!r} must be an integer."
        ) from exc
    if value < 0:
        raise IngestionConfigurationError(f"Stability policy field {key!r} cannot be negative.")
    return value


def resolve_stability_policy(raw: dict[str, Any] | None) -> StabilityPolicy:
    """Return a validated policy, raising for an unusable configuration."""

    policy = raw or {}
    if not isinstance(policy, dict):
        raise IngestionConfigurationError("Stability policy must be a JSON object.")

    required_scans = _non_negative_int(
        policy, "required_stable_scans", DEFAULT_REQUIRED_STABLE_SCANS
    )
    if required_scans < 1:
        raise IngestionConfigurationError(
            "Stability policy 'required_stable_scans' must be at least 1."
        )

    return StabilityPolicy(
        required_stable_scans=required_scans,
        min_file_age_seconds=_non_negative_int(policy, "min_file_age_seconds", 0),
        overlap_seconds=_non_negative_int(policy, "overlap_seconds", 0),
    )


def default_supported_formats() -> list[str]:
    return ["csv", "json", "xlsx"]


def default_stability_policy() -> dict[str, int]:
    return {"required_stable_scans": DEFAULT_REQUIRED_STABLE_SCANS}


def default_mapping() -> dict[str, Any]:
    return {}


class StorageBackend(models.TextChoices):
    """Where discovered objects live."""

    LOCAL = "local", "Local filesystem"
    EFS = "efs", "EFS mounted filesystem"
    S3 = "s3", "Amazon S3"


class ScanStatus(models.TextChoices):
    """Completion state of one rule scan."""

    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    SUCCESS = "success", "Success"
    FAILED = "failed", "Failed"


class IngestionState(models.TextChoices):
    """Lifecycle state of one discovered file."""

    DISCOVERED = "discovered", "Discovered"
    QUEUED = "queued", "Queued"
    PROCESSING = "processing", "Processing"
    PARTIAL_SUCCESS = "partial_success", "Partial success"
    SUCCESS = "success", "Success"
    FAILED = "failed", "Failed"
    SKIPPED = "skipped", "Skipped"
    RETRYING = "retrying", "Retrying"


class IngestionRule(models.Model):
    """Administrator-controlled discovery rule for one storage location."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=128)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ingestion_rules",
        help_text="Optional owner; null means an administrative system scope.",
    )
    storage_backend = models.CharField(
        max_length=20,
        choices=StorageBackend.choices,
        default=StorageBackend.LOCAL,
    )
    location_reference = models.CharField(
        max_length=512,
        help_text="Relative path under INGESTION_LOCAL_ROOT for local/EFS rules.",
    )
    file_pattern = models.CharField(max_length=255, default="*")
    is_enabled = models.BooleanField(default=True)
    supported_formats = models.JSONField(default=default_supported_formats, blank=True)
    schema_mapping = models.JSONField(default=default_mapping, blank=True)
    instrument_mapping = models.JSONField(default=default_mapping, blank=True)
    exchange_mapping = models.JSONField(default=default_mapping, blank=True)
    scan_interval_seconds = models.PositiveIntegerField(default=300)
    stability_policy = models.JSONField(default=default_stability_policy, blank=True)
    initial_watermark = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Files modified at or before this value are treated as historical. "
            "Defaults to the rule creation time on the first scan."
        ),
    )
    last_scan_watermark = models.DateTimeField(null=True, blank=True)
    last_scan_started_at = models.DateTimeField(null=True, blank=True)
    last_scan_completed_at = models.DateTimeField(null=True, blank=True)
    last_scan_status = models.CharField(
        max_length=20,
        choices=ScanStatus.choices,
        default=ScanStatus.PENDING,
    )
    last_scan_error = models.TextField(blank=True, default="")
    last_scan_summary = models.JSONField(default=default_mapping, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "mda_ingestion_rules"
        ordering = ["name", "id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(scan_interval_seconds__gte=1),
                name="ingestion_rule_interval_positive",
            )
        ]
        indexes = [
            models.Index(
                fields=["is_enabled", "last_scan_completed_at"],
                name="idx_ing_rule_enabled_scan",
            )
        ]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()
        self.location_reference = (self.location_reference or "").strip()
        self.file_pattern = (self.file_pattern or "").strip()

        if not self.location_reference:
            raise ValidationError(
                {"location_reference": "A storage location reference is required."}
            )
        if not self.file_pattern:
            raise ValidationError({"file_pattern": "A file pattern is required."})
        pattern_path = Path(self.file_pattern)
        if pattern_path.is_absolute() or ".." in pattern_path.parts:
            raise ValidationError(
                {
                    "file_pattern": "File patterns cannot be absolute or traverse outside the location."
                }
            )

        if self.storage_backend in {StorageBackend.LOCAL, StorageBackend.EFS}:
            reference = Path(self.location_reference)
            if reference.is_absolute():
                raise ValidationError(
                    {
                        "location_reference": "Local locations must be relative to the configured root."
                    }
                )
            if ".." in reference.parts:
                raise ValidationError(
                    {"location_reference": "Local locations cannot traverse outside the root."}
                )

        formats = self.supported_formats or []
        if not isinstance(formats, list) or not formats:
            raise ValidationError(
                {"supported_formats": "Provide at least one supported file format."}
            )
        invalid_formats = {str(value).lower() for value in formats} - set(SUPPORTED_FILE_FORMATS)
        if invalid_formats:
            allowed = ", ".join(SUPPORTED_FILE_FORMATS)
            raise ValidationError(
                {
                    "supported_formats": (
                        f"Unsupported format(s): {', '.join(sorted(invalid_formats))}. "
                        f"Supported formats: {allowed}."
                    )
                }
            )

        try:
            resolve_stability_policy(self.stability_policy)
        except IngestionConfigurationError as exc:
            raise ValidationError({"stability_policy": str(exc)}) from exc


class IngestionObjectState(models.Model):
    """Cross-scan evidence for one object discovered by a rule."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rule = models.ForeignKey(IngestionRule, on_delete=models.CASCADE, related_name="object_states")
    object_key = models.CharField(max_length=512)
    object_name = models.CharField(max_length=255)
    observed_size_bytes = models.PositiveBigIntegerField(default=0)
    observed_modified_at = models.DateTimeField(null=True, blank=True)
    stable_scan_count = models.PositiveIntegerField(default=0)
    is_historical = models.BooleanField(
        default=False,
        help_text="Excluded because it predates the rule's initial watermark.",
    )
    first_seen_at = models.DateTimeField()
    last_seen_at = models.DateTimeField()
    last_content_hash = models.CharField(max_length=64, blank=True, default="")
    last_processed_content_hash = models.CharField(max_length=64, blank=True, default="")
    last_processed_modified_at = models.DateTimeField(null=True, blank=True)
    last_ingested_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "mda_ingestion_object_states"
        ordering = ["rule", "object_key"]
        constraints = [
            models.UniqueConstraint(
                fields=["rule", "object_key"],
                name="unique_ingestion_object_state",
            )
        ]
        indexes = [
            models.Index(
                fields=["rule", "last_seen_at"],
                name="idx_ing_object_rule_seen",
            )
        ]

    def __str__(self) -> str:
        return f"{self.rule.name}: {self.object_key}"


class Ingestion(models.Model):
    """One discovered file and its independent processing lifecycle."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rule = models.ForeignKey(IngestionRule, on_delete=models.PROTECT, related_name="ingestions")
    storage_backend = models.CharField(max_length=20, choices=StorageBackend.choices)
    object_key = models.CharField(max_length=512)
    file_name = models.CharField(max_length=255)
    file_size_bytes = models.PositiveBigIntegerField()
    file_modified_at = models.DateTimeField()
    content_hash = models.CharField(max_length=64)
    state = models.CharField(
        max_length=20,
        choices=IngestionState.choices,
        default=IngestionState.DISCOVERED,
    )
    celery_task_id = models.CharField(max_length=64, blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error_summary = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "mda_ingestions"
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["rule", "object_key", "content_hash"],
                name="unique_ingestion_rule_object_hash",
            )
        ]
        indexes = [
            models.Index(
                fields=["state", "-created_at"],
                name="idx_ingestion_state_time",
            ),
            models.Index(fields=["content_hash"], name="idx_ingestion_content_hash"),
            models.Index(
                fields=["rule", "-created_at"],
                name="idx_ingestion_rule_time",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.file_name} ({self.state})"


class IngestionStateTransition(models.Model):
    """Append-only trace of an ingestion record's state changes."""

    id = models.BigAutoField(primary_key=True)
    ingestion = models.ForeignKey(
        Ingestion, on_delete=models.CASCADE, related_name="state_transitions"
    )
    from_state = models.CharField(max_length=20, blank=True, default="")
    to_state = models.CharField(max_length=20, choices=IngestionState.choices)
    reason = models.CharField(max_length=255, blank=True, default="")
    metadata = models.JSONField(default=default_mapping, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "mda_ingestion_state_transitions"
        ordering = ["ingestion", "created_at", "id"]
        indexes = [
            models.Index(
                fields=["ingestion", "created_at"],
                name="idx_ing_transition_ing_time",
            )
        ]

    def __str__(self) -> str:
        return f"{self.from_state or '-'} -> {self.to_state}"
