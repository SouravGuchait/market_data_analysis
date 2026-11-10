"""Serializers used to document health responses in OpenAPI."""

from __future__ import annotations

from rest_framework import serializers


class DependencyCheckSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["ok", "unavailable"])


class HealthChecksSerializer(serializers.Serializer):
    database = DependencyCheckSerializer(required=False)
    cache = DependencyCheckSerializer(required=False)


class HealthDataSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["ok", "degraded"])
    version = serializers.CharField()
    timestamp = serializers.DateTimeField()
    checks = HealthChecksSerializer(required=False)


class HealthErrorsSerializer(serializers.Serializer):
    database = serializers.ListField(child=serializers.CharField(), required=False)
    cache = serializers.ListField(child=serializers.CharField(), required=False)


class HealthResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()
    # ``data`` and ``errors`` are response keys required by the API contract.
    # DRF uses the same names for Serializer properties, so the type checker
    # needs an explicit exemption for these two declarations.
    data = HealthDataSerializer()  # pyright: ignore[reportIncompatibleMethodOverride, reportAssignmentType]
    errors = HealthErrorsSerializer(  # pyright: ignore[reportIncompatibleMethodOverride, reportAssignmentType]
        allow_null=True
    )
