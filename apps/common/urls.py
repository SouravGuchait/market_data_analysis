"""URLs for operational endpoints."""

from __future__ import annotations

from django.urls import path

from .views import HealthLivenessView, HealthReadinessView

app_name = "health"

urlpatterns = [
    path("", HealthLivenessView.as_view(), name="live"),
    path("ready/", HealthReadinessView.as_view(), name="ready"),
]
