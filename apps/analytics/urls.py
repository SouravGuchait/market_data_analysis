"""Analytics API routes under /api/v1/analytics/."""

from __future__ import annotations

from django.urls import path

from apps.analytics.views import (
    AggregationCreateView,
    AggregationDetailView,
    CandleListView,
    EmaListView,
    MacdListView,
    ReturnsListView,
    SmaListView,
)

urlpatterns = [
    path("candles/", CandleListView.as_view(), name="analytics-candles"),
    path("returns/", ReturnsListView.as_view(), name="analytics-returns"),
    path("sma/", SmaListView.as_view(), name="analytics-sma"),
    path("ema/", EmaListView.as_view(), name="analytics-ema"),
    path("macd/", MacdListView.as_view(), name="analytics-macd"),
    path("aggregations/", AggregationCreateView.as_view(), name="analytics-aggregations"),
    path(
        "aggregations/<uuid:run_id>/",
        AggregationDetailView.as_view(),
        name="analytics-aggregation-detail",
    ),
]
