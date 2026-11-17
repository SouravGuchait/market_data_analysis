"""Owner-scoped reads for validation configurations."""

from __future__ import annotations

import uuid

from django.contrib.auth.base_user import AbstractBaseUser
from django.db.models import Prefetch, QuerySet

from .models import UserValidationConfiguration, ValidationConfigurationVersion


def list_owned_configurations(user: AbstractBaseUser) -> QuerySet[UserValidationConfiguration]:
    """Return configurations belonging to the authenticated user."""

    return UserValidationConfiguration.objects.filter(user=user).select_related(
        "current_version",
        "current_version__approved_by",
    )


def get_owned_configuration(
    user: AbstractBaseUser,
    configuration_id: uuid.UUID,
    *,
    include_history: bool = False,
) -> UserValidationConfiguration | None:
    """Fetch an owned configuration, optionally preloading all versions."""

    queryset = list_owned_configurations(user)
    if include_history:
        queryset = queryset.prefetch_related(
            Prefetch(
                "versions",
                queryset=ValidationConfigurationVersion.objects.select_related("approved_by"),
            )
        )
    return queryset.filter(pk=configuration_id).first()
