"""Portfolio API routes mounted under ``/api/v1/portfolios/``."""

from __future__ import annotations

from django.urls import path

from apps.portfolios.views import (
    PortfolioDetailView,
    PortfolioInstrumentDetailView,
    PortfolioInstrumentListCreateView,
    PortfolioListCreateView,
)

app_name = "portfolios"

urlpatterns = [
    path("", PortfolioListCreateView.as_view(), name="portfolio-list"),
    path("<uuid:portfolio_id>/", PortfolioDetailView.as_view(), name="portfolio-detail"),
    path(
        "<uuid:portfolio_id>/instruments/",
        PortfolioInstrumentListCreateView.as_view(),
        name="portfolio-instrument-list",
    ),
    path(
        "<uuid:portfolio_id>/instruments/<uuid:portfolio_instrument_id>/",
        PortfolioInstrumentDetailView.as_view(),
        name="portfolio-instrument-detail",
    ),
]
