"""Audit API routes mounted under ``/api/v1/audit/``."""

from __future__ import annotations

from django.urls import path

from .views import AuditEventDetailView, AuditEventListView

app_name = "audit"

urlpatterns = [
    path("", AuditEventListView.as_view(), name="audit-list"),
    path("<uuid:audit_id>/", AuditEventDetailView.as_view(), name="audit-detail"),
]
