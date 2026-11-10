"""Routes for the versioned API."""

from __future__ import annotations

from django.urls import include, path

urlpatterns = [
    path("health/", include("apps.common.urls")),
]
