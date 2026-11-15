"""Dashboard routes under /api/v1/dashboard/."""

from __future__ import annotations

from django.urls import path

from apps.dashboard.views import DashboardBootstrapView

urlpatterns = [
    path("bootstrap/", DashboardBootstrapView.as_view(), name="dashboard-bootstrap"),
]
