"""Transactional validation-configuration lifecycle operations."""

from __future__ import annotations

import uuid
from collections.abc import Mapping

from django.contrib.auth.base_user import AbstractBaseUser
from django.db import transaction
from django.utils import timezone

from .models import (
    UserValidationConfiguration,
    ValidationConfigurationStatus,
    ValidationConfigurationVersion,
)
from .validators import validate_compiled_config


class ConfigurationNotFoundError(ValueError):
    """Raised when the authenticated owner has no matching configuration."""


class ConfigurationConflictError(ValueError):
    """Raised when a requested lifecycle transition is not allowed."""


def create_configuration(
    user: AbstractBaseUser, *, asset_type: str, description: str
) -> UserValidationConfiguration:
    """Create one owner/asset configuration and its initial draft version."""

    with transaction.atomic():
        configuration = UserValidationConfiguration.objects.create(
            user=user,
            asset_type=asset_type,
        )
        version = ValidationConfigurationVersion.objects.create(
            configuration=configuration,
            version=1,
            description=description,
        )
        configuration.current_version = version
        configuration.save(update_fields=["current_version", "updated_at"])
    return configuration


def _lock_owned_configuration(
    user: AbstractBaseUser, configuration_id: uuid.UUID
) -> tuple[UserValidationConfiguration, ValidationConfigurationVersion]:
    configuration = (
        UserValidationConfiguration.objects.select_for_update()
        .filter(user=user, pk=configuration_id)
        .first()
    )
    if configuration is None:
        raise ConfigurationNotFoundError
    current = configuration.current_version
    if current is None:
        raise ConfigurationConflictError("Configuration has no current version.")
    version = (
        ValidationConfigurationVersion.objects.select_for_update()
        .filter(configuration=configuration, pk=current.pk)
        .first()
    )
    if version is None:
        raise ConfigurationConflictError("Configuration has no current version.")
    return configuration, version


def update_configuration(
    user: AbstractBaseUser,
    configuration_id: uuid.UUID,
    changes: Mapping[str, object],
) -> UserValidationConfiguration:
    """Edit a draft or start a new draft after an approved version."""

    with transaction.atomic():
        configuration, current = _lock_owned_configuration(user, configuration_id)
        if current.status == ValidationConfigurationStatus.NEEDS_REVIEW:
            raise ConfigurationConflictError("Reject the pending review before editing.")
        description = changes.get("description", current.description)
        if not isinstance(description, str):
            raise ConfigurationConflictError("Description must be a string.")

        if current.status == ValidationConfigurationStatus.APPROVED:
            version = ValidationConfigurationVersion.objects.create(
                configuration=configuration,
                version=current.version + 1,
                description=description,
            )
        elif current.status in {
            ValidationConfigurationStatus.DRAFT,
            ValidationConfigurationStatus.REJECTED,
        }:
            version = _edit_unapproved_version(current, description)
        else:
            raise ConfigurationConflictError("This configuration cannot be edited.")

        configuration.current_version = version
        configuration.save(update_fields=["current_version", "updated_at"])
    return configuration


def _edit_unapproved_version(
    version: ValidationConfigurationVersion, description: str
) -> ValidationConfigurationVersion:
    version.description = description
    fields = ["description", "updated_at"]
    if version.status == ValidationConfigurationStatus.REJECTED:
        version.status = ValidationConfigurationStatus.DRAFT
        version.compiled_config = None
        version.approved_at = None
        version.approved_by = None
        fields.extend(["status", "compiled_config", "approved_at", "approved_by"])
    version.save(update_fields=fields)
    return version


def import_compiled_config(
    user: AbstractBaseUser,
    configuration_id: uuid.UUID,
    raw_config: object,
) -> tuple[UserValidationConfiguration, ValidationConfigurationVersion]:
    """Validate a manual JSON import and move the current draft to review."""

    with transaction.atomic():
        configuration, version = _lock_owned_configuration(user, configuration_id)
        if version.status != ValidationConfigurationStatus.DRAFT:
            raise ConfigurationConflictError("Only a draft can receive compiled JSON.")
        compiled_config = validate_compiled_config(raw_config, configuration.asset_type)
        version.compiled_config = compiled_config
        version.status = ValidationConfigurationStatus.NEEDS_REVIEW
        version.save(update_fields=["compiled_config", "status", "updated_at"])
    configuration.current_version = version
    return configuration, version


def approve_configuration(
    user: AbstractBaseUser, configuration_id: uuid.UUID
) -> tuple[UserValidationConfiguration, ValidationConfigurationVersion]:
    """Approve the current validated snapshot and record its reviewer."""

    with transaction.atomic():
        configuration, version = _lock_owned_configuration(user, configuration_id)
        if (
            version.status != ValidationConfigurationStatus.NEEDS_REVIEW
            or version.compiled_config is None
        ):
            raise ConfigurationConflictError("A validated configuration must be reviewed first.")
        version.compiled_config = validate_compiled_config(
            version.compiled_config,
            configuration.asset_type,
        )
        version.status = ValidationConfigurationStatus.APPROVED
        version.approved_at = timezone.now()
        version.approved_by = user
        version.save(
            update_fields=[
                "compiled_config",
                "status",
                "approved_at",
                "approved_by",
                "updated_at",
            ]
        )
    configuration.current_version = version
    return configuration, version


def reject_configuration(
    user: AbstractBaseUser, configuration_id: uuid.UUID
) -> tuple[UserValidationConfiguration, ValidationConfigurationVersion]:
    """Reject a pending review without changing previously approved versions."""

    with transaction.atomic():
        configuration, version = _lock_owned_configuration(user, configuration_id)
        if version.status != ValidationConfigurationStatus.NEEDS_REVIEW:
            raise ConfigurationConflictError("Only a pending review can be rejected.")
        version.status = ValidationConfigurationStatus.REJECTED
        version.save(update_fields=["status", "updated_at"])
    configuration.current_version = version
    return configuration, version
