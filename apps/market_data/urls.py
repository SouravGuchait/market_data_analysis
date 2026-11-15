"""Market data routes under /api/v1/market-data/."""

from __future__ import annotations

from django.urls import path

from apps.market_data.views import DailyOHLCVListView, TickListView

urlpatterns = [
    path("ticks/", TickListView.as_view(), name="market-data-ticks"),
    path("daily-ohlcv/", DailyOHLCVListView.as_view(), name="market-data-daily-ohlcv"),
]
