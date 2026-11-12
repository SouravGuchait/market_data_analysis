"""Instrument API routes mounted under ``/api/v1/instruments/``."""

from __future__ import annotations

from django.urls import path

from apps.instruments.views import InstrumentListCreateView

app_name = "instruments"

urlpatterns = [
    path("", InstrumentListCreateView.as_view(), name="instrument-list"),
]
