"""Serializers for audit records and query filters."""

from __future__ import annotations

from datetime import datetime, time

from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import serializers

from .models import AuditEvent, AuditStatus


class AuditActorSerializer(serializers.Serializer):
    """Minimal public actor representation; never expose credentials."""

    id = serializers.CharField(source="pk", read_only=True)
    username = serializers.CharField(read_only=True)


class AuditEventReadSerializer(serializers.ModelSerializer):
    actor = AuditActorSerializer(read_only=True, allow_null=True)

    class Meta:
        model = AuditEvent
        fields = (
            "id",
            "actor",
            "action",
            "resource_type",
            "resource_id",
            "status",
            "request_id",
            "source_ip",
            "metadata",
            "created_at",
        )
        read_only_fields = fields


def _parse_filter_datetime(value: str, *, end_of_range: bool) -> datetime:
    """Parse an ISO date or datetime filter into an aware datetime."""

    text = value.strip()
    if not text:
        raise serializers.ValidationError("Provide an ISO date or datetime.")

    parsed_date = parse_date(text)
    if parsed_date is not None:
        bound = time.max if end_of_range else time.min
        return timezone.make_aware(
            datetime.combine(parsed_date, bound),
            timezone.get_current_timezone(),
        )

    try:
        parsed_datetime = parse_datetime(text)
    except ValueError as exc:
        raise serializers.ValidationError("Provide a valid ISO date or datetime.") from exc

    if parsed_datetime is None:
        raise serializers.ValidationError("Provide a valid ISO date or datetime.")
    if timezone.is_naive(parsed_datetime):
        parsed_datetime = timezone.make_aware(
            parsed_datetime,
            timezone.get_current_timezone(),
        )
    return parsed_datetime


class AuditEventFilterSerializer(serializers.Serializer):
    """Validate supported list filters from query parameters."""

    action = serializers.CharField(required=False, max_length=64)
    resource_type = serializers.CharField(required=False, max_length=64)
    status = serializers.ChoiceField(choices=AuditStatus.choices, required=False)
    start_date = serializers.CharField(required=False)
    end_date = serializers.CharField(required=False)

    def validate_start_date(self, value: str) -> datetime:
        return _parse_filter_datetime(value, end_of_range=False)

    def validate_end_date(self, value: str) -> datetime:
        return _parse_filter_datetime(value, end_of_range=True)

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        start_date = attrs.get("start_date")
        end_date = attrs.get("end_date")
        if (
            isinstance(start_date, datetime)
            and isinstance(end_date, datetime)
            and start_date > end_date
        ):
            raise serializers.ValidationError(
                {"end_date": ["end_date must be greater than or equal to start_date."]}
            )
        return attrs
