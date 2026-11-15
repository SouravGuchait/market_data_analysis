"""Analytics API routes under /api/v1/analytics/."""

from __future__ import annotations

from django.urls import path

from apps.analytics.views import (
    AggregationCreateView,
    AggregationDetailView,
    AtrListView,
    BollingerListView,
    CandleListView,
    DrawdownListView,
    EmaListView,
    MacdListView,
    OverviewListView,
    ReturnsListView,
    RsiListView,
    SmaListView,
    StochasticListView,
)

urlpatterns = [
    path("candles/", CandleListView.as_view(), name="analytics-candles"),
    path("returns/", ReturnsListView.as_view(), name="analytics-returns"),
    path("drawdown/", DrawdownListView.as_view(), name="analytics-drawdown"),
    path("overview/", OverviewListView.as_view(), name="analytics-overview"),
    path("sma/", SmaListView.as_view(), name="analytics-sma"),
    path("ema/", EmaListView.as_view(), name="analytics-ema"),
    path("macd/", MacdListView.as_view(), name="analytics-macd"),
    path("bollinger/", BollingerListView.as_view(), name="analytics-bollinger"),
    path("atr/", AtrListView.as_view(), name="analytics-atr"),
    path("rsi/", RsiListView.as_view(), name="analytics-rsi"),
    path("stochastic/", StochasticListView.as_view(), name="analytics-stochastic"),
    path("aggregations/", AggregationCreateView.as_view(), name="analytics-aggregations"),
    path(
        "aggregations/<uuid:run_id>/",
        AggregationDetailView.as_view(),
        name="analytics-aggregation-detail",
    ),
]
