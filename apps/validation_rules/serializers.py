"""Read and write serializers for validation configuration APIs."""

from __future__ import annotations

from rest_framework import serializers

from .models import (
    UserValidationConfiguration,
    ValidationAssetType,
    ValidationConfigurationVersion,
)
from .schema import MAX_DESCRIPTION_LENGTH


class ApprovedBySerializer(serializers.Serializer):
    """Safe public identity fields for the approving user."""

    id = serializers.CharField(source="pk", read_only=True)
    username = serializers.CharField(read_only=True)


class ValidationConfigurationVersionReadSerializer(serializers.ModelSerializer):
    """Read representation of one retained configuration version."""

    approved_by = ApprovedBySerializer(read_only=True, allow_null=True)

    class Meta:
        model = ValidationConfigurationVersion
        fields = (
            "id",
            "version",
            "description",
            "compiled_config",
            "status",
            "approved_by",
            "approved_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class ValidationConfigurationReadSerializer(serializers.ModelSerializer):
    """Current version and stable identity for list responses."""

    current_version = ValidationConfigurationVersionReadSerializer(read_only=True)

    class Meta:
        model = UserValidationConfiguration
        fields = ("id", "asset_type", "current_version", "created_at", "updated_at")
        read_only_fields = fields


class ValidationConfigurationDetailReadSerializer(serializers.ModelSerializer):
    """Current configuration plus retained versions for detail responses."""

    current_version = ValidationConfigurationVersionReadSerializer(read_only=True)
    versions = ValidationConfigurationVersionReadSerializer(many=True, read_only=True)

    class Meta:
        model = UserValidationConfiguration
        fields = (
            "id",
            "asset_type",
            "current_version",
            "versions",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class ValidationConfigurationCreateSerializer(serializers.Serializer):
    """Create a natural-language draft for one asset type."""

    asset_type = serializers.ChoiceField(choices=ValidationAssetType.choices)
    description = serializers.CharField(max_length=MAX_DESCRIPTION_LENGTH)


class ValidationConfigurationPatchSerializer(serializers.Serializer):
    """Edit only the natural-language description of an unapproved draft."""

    description = serializers.CharField(max_length=MAX_DESCRIPTION_LENGTH, required=False)

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        if not attrs:
            raise serializers.ValidationError("Provide a description to update.")
        return attrs


class ValidationSchemaQuerySerializer(serializers.Serializer):
    """Validate the asset-type query parameter for schema discovery."""

    asset_type = serializers.ChoiceField(choices=ValidationAssetType.choices)
