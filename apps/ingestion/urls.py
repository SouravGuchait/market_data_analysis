"""URL routes for user ingestion management."""

from __future__ import annotations

from django.urls import path

from .views import (
    ConfiguredImportView,
    IngestionDetailView,
    IngestionListView,
    IngestionLogListView,
    IngestionRejectionListView,
    IngestionRetryView,
    IngestionRuleDetailView,
    IngestionRuleListCreateView,
    StorageFileListView,
    ValidationRuleSetCreateView,
)

urlpatterns = [
    path("files/import/", ConfiguredImportView.as_view(), name="ingestion-file-import"),
    path("logs/", IngestionLogListView.as_view(), name="ingestion-logs"),
    path("files/", StorageFileListView.as_view(), name="ingestion-file-list"),
    path("rules/", IngestionRuleListCreateView.as_view(), name="ingestion-rule-list"),
    path(
        "validation-rule-sets/",
        ValidationRuleSetCreateView.as_view(),
        name="ingestion-validation-rule-set-create",
    ),
    path("rules/<uuid:rule_id>/", IngestionRuleDetailView.as_view(), name="ingestion-rule-detail"),
    path(
        "<uuid:ingestion_id>/rejections/",
        IngestionRejectionListView.as_view(),
        name="ingestion-rejections",
    ),
    path("<uuid:ingestion_id>/retry/", IngestionRetryView.as_view(), name="ingestion-retry"),
    path("<uuid:ingestion_id>/", IngestionDetailView.as_view(), name="ingestion-detail"),
    path("", IngestionListView.as_view(), name="ingestion-list"),
]
