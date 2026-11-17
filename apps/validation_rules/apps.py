"""Django application configuration for user validation rules."""

from __future__ import annotations

from django.apps import AppConfig


class ValidationRulesConfig(AppConfig):
    """Register persisted user-owned validation configurations."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.validation_rules"
