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

SUPPORTED_FILE_FORMATS: tuple[str, ...] = ("csv", "json", "jsonl", "xlsx")
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


def _safe_storage_prefix(value: str) -> bool:
    prefix = value.strip()
    if not prefix or prefix in {".", ".."} or "/" in prefix or "\\" in prefix:
        return False
    return prefix.replace("-", "").replace("_", "").isalnum()


class StorageBackend(models.TextChoices):
    """Where discovered objects live."""

    LOCAL = "local", "Local filesystem"
    EFS = "efs", "EFS mounted filesystem"
    S3 = "s3", "Amazon S3"


class DataType(models.TextChoices):
    """File profile selected by an ingestion rule."""

    DAILY_OHLCV = "DAILY_OHLCV", "Daily OHLCV"
    TICK_DATA = "TICK_DATA", "Tick data"


class DetectedFormat(models.TextChoices):
    """Format inferred from a stored file name."""

    CSV = "csv", "CSV"
    JSON = "json", "JSON"
    JSONL = "jsonl", "JSONL"
    XLSX = "xlsx", "XLSX"


DEFAULT_CHUNK_SIZE = 5000
MAX_CHUNK_SIZE_CEILING = 1_000_000


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
    CANCELLED = "cancelled", "Cancelled"


class StorageFile(models.Model):
    """One file stored for a user under the configured ingestion root."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="storage_files",
    )
    storage_backend = models.CharField(
        max_length=20,
        choices=StorageBackend.choices,
        default=StorageBackend.LOCAL,
    )
    storage_key = models.CharField(max_length=512)
    original_filename = models.CharField(max_length=255)
    file_size = models.PositiveBigIntegerField()
    content_hash = models.CharField(max_length=64)
    detected_format = models.CharField(max_length=8, choices=DetectedFormat.choices)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "mda_storage_files"
        ordering = ["-uploaded_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "storage_key"],
                name="unique_storage_file_owner_key",
            )
        ]
        indexes = [
            models.Index(fields=["owner", "-uploaded_at"], name="idx_storage_file_owner_time"),
            models.Index(fields=["content_hash"], name="idx_storage_file_hash"),
        ]

    def __str__(self) -> str:
        return self.original_filename


class IngestionRule(models.Model):
    """Discovery rule for one storage location.

    A null owner is an administrative rule scanned by the scheduler. A rule
    with an owner is scanned only by that user's authenticated API request.
    """

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
    data_type = models.CharField(max_length=20, blank=True, default="")
    storage_prefix = models.CharField(max_length=64, blank=True, default="")
    parser_config = models.JSONField(default=default_mapping, blank=True)
    validation_rule_set_id = models.UUIDField(null=True, blank=True)
    chunk_size = models.PositiveIntegerField(default=DEFAULT_CHUNK_SIZE)
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
            ),
            models.CheckConstraint(
                condition=Q(chunk_size__gte=1) & Q(chunk_size__lte=MAX_CHUNK_SIZE_CEILING),
                name="ingestion_rule_chunk_size_bounds",
            ),
            models.CheckConstraint(
                condition=Q(data_type="")
                | Q(data_type__in=[DataType.DAILY_OHLCV, DataType.TICK_DATA]),
                name="ingestion_rule_data_type_known",
            ),
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
        self._clean_paths()
        self._clean_formats()
        self._clean_options()

    def _clean_paths(self) -> None:
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
        if self.storage_backend not in {StorageBackend.LOCAL, StorageBackend.EFS}:
            return
        reference = Path(self.location_reference)
        if reference.is_absolute() or ".." in reference.parts:
            raise ValidationError(
                {"location_reference": ("Local locations must be relative to the configured root.")}
            )

    def _clean_formats(self) -> None:
        formats = self.supported_formats or []
        if not isinstance(formats, list) or not formats:
            raise ValidationError(
                {"supported_formats": "Provide at least one supported file format."}
            )
        invalid_formats = {str(value).lower() for value in formats} - set(SUPPORTED_FILE_FORMATS)
        if not invalid_formats:
            return
        allowed = ", ".join(SUPPORTED_FILE_FORMATS)
        raise ValidationError(
            {
                "supported_formats": (
                    f"Unsupported format(s): {', '.join(sorted(invalid_formats))}. "
                    f"Supported formats: {allowed}."
                )
            }
        )

    def _clean_options(self) -> None:
        if self.data_type and self.data_type not in {DataType.DAILY_OHLCV, DataType.TICK_DATA}:
            raise ValidationError({"data_type": "Data type must be DAILY_OHLCV or TICK_DATA."})
        if self.storage_prefix and not _safe_storage_prefix(self.storage_prefix):
            raise ValidationError(
                {"storage_prefix": "Storage prefix must be a single relative path segment."}
            )
        if not isinstance(self.parser_config, dict):
            raise ValidationError({"parser_config": "Parser configuration must be a JSON object."})
        self._clean_chunk_size()
        try:
            resolve_stability_policy(self.stability_policy)
        except IngestionConfigurationError as exc:
            raise ValidationError({"stability_policy": str(exc)}) from exc

    def _clean_chunk_size(self) -> None:
        minimum = settings.INGESTION_MIN_CHUNK_SIZE
        maximum = min(settings.INGESTION_MAX_CHUNK_SIZE, MAX_CHUNK_SIZE_CEILING)
        if self.chunk_size < minimum or self.chunk_size > maximum:
            raise ValidationError(
                {"chunk_size": f"Chunk size must be between {minimum} and {maximum}."}
            )


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
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="ingestions",
    )
    storage_file = models.ForeignKey(
        StorageFile,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ingestions",
    )
    storage_backend = models.CharField(max_length=20, choices=StorageBackend.choices)
    object_key = models.CharField(max_length=512)
    file_name = models.CharField(max_length=255)
    file_size_bytes = models.PositiveBigIntegerField()
    file_modified_at = models.DateTimeField()
    content_hash = models.CharField(max_length=64)
    data_type = models.CharField(max_length=20, blank=True, default="")
    validation_rule_set_version = models.CharField(max_length=64, blank=True, default="")
    chunk_size = models.PositiveIntegerField(default=DEFAULT_CHUNK_SIZE)
    reprocess_version = models.PositiveIntegerField(default=1)
    rows_read = models.PositiveIntegerField(default=0)
    accepted_rows = models.PositiveIntegerField(default=0)
    rejected_rows = models.PositiveIntegerField(default=0)
    duplicate_rows = models.PositiveIntegerField(default=0)
    chunks_processed = models.PositiveIntegerField(default=0)
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
                fields=["rule", "object_key", "content_hash", "reprocess_version"],
                name="unique_ingestion_rule_object_hash",
            ),
            models.CheckConstraint(
                condition=Q(data_type="")
                | Q(data_type__in=[DataType.DAILY_OHLCV, DataType.TICK_DATA]),
                name="ingestion_data_type_known",
            ),
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
            models.Index(
                fields=["owner", "state", "-created_at"],
                name="idx_ingestion_owner_state",
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


class IngestionScan(models.Model):
    """One authenticated manual scan of a user-owned rule."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ingestion_scans",
    )
    rule = models.ForeignKey(IngestionRule, on_delete=models.CASCADE, related_name="scans")
    status = models.CharField(max_length=20, choices=ScanStatus.choices, default=ScanStatus.PENDING)
    discovered_count = models.PositiveIntegerField(default=0)
    queued_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "mda_ingestion_scans"
        ordering = ["-started_at", "-id"]
        indexes = [
            models.Index(fields=["owner", "-started_at"], name="idx_ing_scan_owner_time"),
        ]

    def __str__(self) -> str:
        return f"{self.rule.name} ({self.status})"


class ValidatorType(models.TextChoices):
    """Supported row validators. Arbitrary expressions are never executed."""

    REQUIRED = "REQUIRED", "Required"
    NUMERIC = "NUMERIC", "Numeric"
    POSITIVE = "POSITIVE", "Positive"
    NON_NEGATIVE = "NON_NEGATIVE", "Non-negative"
    MIN_VALUE = "MIN_VALUE", "Minimum value"
    MAX_VALUE = "MAX_VALUE", "Maximum value"
    MAX_DECIMAL_PLACES = "MAX_DECIMAL_PLACES", "Maximum decimal places"
    DATETIME = "DATETIME", "Datetime"
    ENUM = "ENUM", "Enumeration"
    OHLC_RELATIONSHIP = "OHLC_RELATIONSHIP", "OHLC relationship"
    INSTRUMENT_EXISTS = "INSTRUMENT_EXISTS", "Instrument exists"
    EXCHANGE_EXISTS = "EXCHANGE_EXISTS", "Exchange exists"
    EXCHANGE_INSTRUMENT_MATCH = "EXCHANGE_INSTRUMENT_MATCH", "Exchange/instrument match"
    DUPLICATE_CHECK = "DUPLICATE_CHECK", "Duplicate check"


class ValidationRuleSet(models.Model):
    """Named, versioned set of field validators for one data type."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=128)
    data_type = models.CharField(max_length=20, choices=DataType.choices)
    version = models.CharField(max_length=64, default="1")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "mda_validation_rule_sets"
        ordering = ["name", "version"]
        constraints = [
            models.UniqueConstraint(
                fields=["name", "version"],
                name="unique_validation_rule_set_name_version",
            )
        ]

    def __str__(self) -> str:
        return f"{self.name}@{self.version}"


class ValidationRule(models.Model):
    """One enabled validator belonging to a rule set."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rule_set = models.ForeignKey(
        ValidationRuleSet,
        on_delete=models.CASCADE,
        related_name="rules",
    )
    field_name = models.CharField(max_length=64, blank=True, default="")
    validator = models.CharField(max_length=64, choices=ValidatorType.choices)
    parameters = models.JSONField(default=default_mapping, blank=True)
    is_enabled = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "mda_validation_rules"
        ordering = ["rule_set", "sort_order", "id"]
        indexes = [
            models.Index(fields=["rule_set", "sort_order"], name="idx_validation_rule_order"),
        ]

    def __str__(self) -> str:
        return f"{self.validator}:{self.field_name or '*'}"
