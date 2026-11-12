"""Exchange API routes mounted under ``/api/v1/exchanges/``."""

from __future__ import annotations

from django.urls import path

from apps.exchanges.views import ExchangeListView

app_name = "exchanges"

urlpatterns = [
    path("", ExchangeListView.as_view(), name="exchange-list"),
]
