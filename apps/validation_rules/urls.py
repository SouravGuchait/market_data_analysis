"""Validation configuration routes mounted under ``/api/v1/rules/``."""

from __future__ import annotations

from django.urls import path

from .views import (
    CompiledConfigImportView,
    ValidationConfigurationApproveView,
    ValidationConfigurationDetailView,
    ValidationConfigurationListCreateView,
    ValidationConfigurationRejectView,
    ValidationSchemaView,
)

app_name = "validation_rules"

urlpatterns = [
    path("", ValidationConfigurationListCreateView.as_view(), name="validation-rule-list"),
    path("schema/", ValidationSchemaView.as_view(), name="validation-rule-schema"),
    path(
        "<uuid:configuration_id>/compiled-config/",
        CompiledConfigImportView.as_view(),
        name="validation-rule-compiled-config",
    ),
    path(
        "<uuid:configuration_id>/approve/",
        ValidationConfigurationApproveView.as_view(),
        name="validation-rule-approve",
    ),
    path(
        "<uuid:configuration_id>/reject/",
        ValidationConfigurationRejectView.as_view(),
        name="validation-rule-reject",
    ),
    path(
        "<uuid:configuration_id>/",
        ValidationConfigurationDetailView.as_view(),
        name="validation-rule-detail",
    ),
]
