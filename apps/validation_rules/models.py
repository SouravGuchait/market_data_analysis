"""Persisted validation configurations and immutable approved versions."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel


class ValidationAssetType(models.TextChoices):
    """Canonical ingestion domains supported by custom validation rules."""

    OHLCV = "ohlcv", "Daily OHLCV"
    TICK = "tick", "Tick data"


class ValidationConfigurationStatus(models.TextChoices):
    """Lifecycle states for one configuration version."""

    DRAFT = "draft", "Draft"
    NEEDS_REVIEW = "needs_review", "Needs review"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    FAILED = "failed", "Failed"


class UserValidationConfiguration(TimeStampedModel):
    """Stable owner and asset-type identity for a sequence of rule versions."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="validation_configurations",
    )
    asset_type = models.CharField(max_length=16, choices=ValidationAssetType.choices)
    current_version = models.OneToOneField(
        "ValidationConfigurationVersion",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="current_for",
    )

    class Meta(TimeStampedModel.Meta):
        db_table = "mda_user_validation_configurations"
        ordering = ["asset_type", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "asset_type"],
                name="unique_user_validation_asset_type",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user.pk}:{self.asset_type}"


class ValidationConfigurationVersion(TimeStampedModel):
    """One draft or approved snapshot in a user's configuration history."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    configuration = models.ForeignKey(
        UserValidationConfiguration,
        on_delete=models.CASCADE,
        related_name="versions",
    )
    version = models.PositiveIntegerField()
    description = models.TextField(max_length=5000)
    compiled_config = models.JSONField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=ValidationConfigurationStatus.choices,
        default=ValidationConfigurationStatus.DRAFT,
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_validation_configurations",
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta(TimeStampedModel.Meta):
        db_table = "mda_validation_configuration_versions"
        ordering = ["-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["configuration", "version"],
                name="unique_validation_config_version",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.configuration.pk}:v{self.version}:{self.status}"
