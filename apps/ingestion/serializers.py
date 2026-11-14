"""Request and response serializers for user ingestion management."""

from __future__ import annotations

from datetime import datetime, time
from pathlib import Path

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import serializers

from .models import (
    SUPPORTED_FILE_FORMATS,
    DataType,
    Ingestion,
    IngestionRule,
    IngestionScan,
    IngestionState,
    RejectedRow,
    StorageBackend,
    StorageFile,
    _safe_storage_prefix,
)


def _parse_bound(value: str, *, end_of_range: bool) -> datetime:
    text = value.strip()
    parsed_date = parse_date(text)
    if parsed_date is not None:
        clock = time.max if end_of_range else time.min
        combined = datetime.combine(parsed_date, clock)
        return timezone.make_aware(combined) if timezone.is_naive(combined) else combined

    parsed = parse_datetime(text)
    if parsed is None:
        raise serializers.ValidationError("Provide an ISO date or datetime.")
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed)
    return parsed


class StorageFileReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = StorageFile
        fields = (
            "id",
            "storage_backend",
            "storage_key",
            "original_filename",
            "file_size",
            "content_hash",
            "detected_format",
            "uploaded_at",
            "is_active",
        )
        read_only_fields = fields


class IngestionRuleReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = IngestionRule
        fields = (
            "id",
            "name",
            "data_type",
            "storage_backend",
            "storage_prefix",
            "file_pattern",
            "parser_config",
            "instrument_mapping",
            "exchange_mapping",
            "validation_rule_set_id",
            "chunk_size",
            "is_enabled",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class IngestionRuleSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = IngestionRule
        fields = ("id", "name", "data_type")
        read_only_fields = fields


class IngestionReadSerializer(serializers.ModelSerializer):
    filename = serializers.CharField(source="file_name", read_only=True)
    status = serializers.CharField(source="state", read_only=True)
    completed_at = serializers.DateTimeField(source="finished_at", read_only=True, allow_null=True)
    rule = IngestionRuleSummarySerializer(read_only=True)

    class Meta:
        model = Ingestion
        fields = (
            "id",
            "filename",
            "data_type",
            "status",
            "rule",
            "rows_read",
            "accepted_rows",
            "rejected_rows",
            "duplicate_rows",
            "chunks_processed",
            "chunk_size",
            "validation_rule_set_version",
            "error_summary",
            "started_at",
            "completed_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class RejectedRowReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = RejectedRow
        fields = (
            "id",
            "source_row_number",
            "reason_code",
            "field_name",
            "error_message",
            "raw_record",
            "rejected_at",
        )
        read_only_fields = fields


class IngestionScanReadSerializer(serializers.ModelSerializer):
    rule_id = serializers.UUIDField(source="rule.pk", read_only=True)
    rule_name = serializers.CharField(source="rule.name", read_only=True)

    class Meta:
        model = IngestionScan
        fields = (
            "id",
            "rule_name",
            "rule_id",
            "status",
            "discovered_count",
            "queued_count",
            "skipped_count",
            "failed_count",
            "started_at",
            "completed_at",
        )
        read_only_fields = fields


class IngestionRuleWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=128)
    data_type = serializers.ChoiceField(choices=DataType.choices)
    storage_backend = serializers.CharField(required=False, default=StorageBackend.LOCAL)
    storage_prefix = serializers.CharField(max_length=64)
    file_pattern = serializers.CharField(max_length=255)
    chunk_size = serializers.IntegerField(required=False, min_value=1)
    is_enabled = serializers.BooleanField(required=False)
    parser_config = serializers.JSONField()
    instrument_mapping = serializers.JSONField(required=False)
    exchange_mapping = serializers.JSONField(required=False)
    validation_rule_set_id = serializers.UUIDField(required=False, allow_null=True)

    def validate_storage_backend(self, value: str) -> str:
        normalized = value.strip().lower()
        if normalized != StorageBackend.LOCAL:
            raise serializers.ValidationError("Only local storage is available.")
        return normalized

    def validate_storage_prefix(self, value: str) -> str:
        prefix = value.strip()
        if not _safe_storage_prefix(prefix):
            raise serializers.ValidationError(
                "Storage prefix must be a single relative path segment."
            )
        return prefix

    def validate_file_pattern(self, value: str) -> str:
        pattern = value.strip()
        pattern_path = Path(pattern)
        if not pattern or pattern_path.is_absolute() or ".." in pattern_path.parts:
            raise serializers.ValidationError(
                "File patterns cannot be empty, absolute or traverse outside the location."
            )
        return pattern

    def validate_chunk_size(self, value: int) -> int:
        minimum = settings.INGESTION_MIN_CHUNK_SIZE
        maximum = settings.INGESTION_MAX_CHUNK_SIZE
        if value < minimum or value > maximum:
            raise serializers.ValidationError(
                f"Chunk size must be between {minimum} and {maximum}."
            )
        return value

    def validate_parser_config(self, value: object) -> dict[str, object]:
        if not isinstance(value, dict):
            raise serializers.ValidationError("Parser configuration must be a JSON object.")
        file_format = str(value.get("format", "csv")).strip().lower()
        if file_format not in SUPPORTED_FILE_FORMATS:
            allowed = ", ".join(SUPPORTED_FILE_FORMATS)
            raise serializers.ValidationError(f"Unsupported format. Supported: {allowed}.")
        normalized = dict(value)
        normalized["format"] = file_format
        return normalized

    def validate_instrument_mapping(self, value: object) -> dict[str, object]:
        return _json_object(value, "Instrument mapping must be a JSON object.")

    def validate_exchange_mapping(self, value: object) -> dict[str, object]:
        return _json_object(value, "Exchange mapping must be a JSON object.")


class IngestionListFilterSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=IngestionState.choices, required=False)
    data_type = serializers.ChoiceField(choices=DataType.choices, required=False)
    rule_id = serializers.UUIDField(required=False)
    filename = serializers.CharField(required=False, allow_blank=True, max_length=255)
    start_date = serializers.CharField(required=False, allow_blank=True)
    end_date = serializers.CharField(required=False, allow_blank=True)

    def validate_start_date(self, value: str) -> datetime | None:
        if not value.strip():
            return None
        return _parse_bound(value, end_of_range=False)

    def validate_end_date(self, value: str) -> datetime | None:
        if not value.strip():
            return None
        return _parse_bound(value, end_of_range=True)


class ConfiguredImportSerializer(serializers.Serializer):
    storage_backends = serializers.ListField(
        child=serializers.ChoiceField(choices=["local", "s3"]),
        required=False,
        allow_empty=False,
    )
    storage_prefix = serializers.CharField(required=False, max_length=64)

    def validate_storage_prefix(self, value: str) -> str:
        prefix = value.strip()
        if not prefix:
            raise serializers.ValidationError(
                "Storage prefix must be a single relative path segment."
            )
        return prefix


class ScanRequestSerializer(serializers.Serializer):
    rule_id = serializers.CharField(max_length=128)

    def validate_rule_id(self, value: str) -> str:
        reference = value.strip()
        if not reference:
            raise serializers.ValidationError("A rule id or rule name is required.")
        return reference


class DispatchRequestSerializer(serializers.Serializer):
    ingestion_ids = serializers.ListField(
        child=serializers.CharField(max_length=255),
        required=False,
        allow_empty=True,
    )

    def validate_ingestion_ids(self, value: list[str]) -> list[str]:
        references = [item.strip() for item in value]
        if any(not item for item in references):
            raise serializers.ValidationError("Each ingestion reference must be a file name or id.")
        return references


def _json_object(value: object, message: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise serializers.ValidationError(message)
    return dict(value)


def default_chunk_size() -> int:
    configured = settings.INGESTION_DEFAULT_CHUNK_SIZE
    minimum = settings.INGESTION_MIN_CHUNK_SIZE
    maximum = settings.INGESTION_MAX_CHUNK_SIZE
    return min(max(configured, minimum), maximum)


def rule_write_payload(validated: dict[str, object], *, creating: bool) -> dict[str, object]:
    """Fill create-only defaults without treating an omitted patch field as a change."""

    payload = dict(validated)
    if creating and "chunk_size" not in payload:
        payload["chunk_size"] = default_chunk_size()
    if creating and "is_enabled" not in payload:
        payload["is_enabled"] = True
    if creating and "instrument_mapping" not in payload:
        payload["instrument_mapping"] = {}
    if creating and "exchange_mapping" not in payload:
        payload["exchange_mapping"] = {}
    if creating and "validation_rule_set_id" not in payload:
        payload["validation_rule_set_id"] = None
    return payload
