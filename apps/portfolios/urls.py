"""Portfolio API routes mounted under ``/api/v1/portfolios/``."""

from __future__ import annotations

from django.urls import path

from apps.portfolios.views import (
    PortfolioByNameView,
    PortfolioDetailView,
    PortfolioInstrumentBulkByNameView,
    PortfolioInstrumentBulkView,
    PortfolioInstrumentDetailView,
    PortfolioInstrumentListCreateView,
    PortfolioListCreateView,
)

app_name = "portfolios"

urlpatterns = [
    path("", PortfolioListCreateView.as_view(), name="portfolio-list"),
    path("by-name/", PortfolioByNameView.as_view(), name="portfolio-by-name"),
    path(
        "by-name/instruments/bulk/",
        PortfolioInstrumentBulkByNameView.as_view(),
        name="portfolio-instrument-bulk-by-name",
    ),
    path("<uuid:portfolio_id>/", PortfolioDetailView.as_view(), name="portfolio-detail"),
    path(
        "<uuid:portfolio_id>/instruments/bulk/",
        PortfolioInstrumentBulkView.as_view(),
        name="portfolio-instrument-bulk",
    ),
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
