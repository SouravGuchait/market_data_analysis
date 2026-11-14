"""Unit tests for daily return calculations from DailyOHLCV."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from math import isclose, log

import pytest

from apps.analytics.services.metrics_calculator import compute_daily_returns
from apps.market_data.models import DailyOHLCV


@pytest.mark.django_db
def test_simple_and_log_returns(instrument) -> None:
    first = DailyOHLCV.objects.create(
        instrument=instrument,
        trading_date=date(2024, 1, 2),
        timestamp=datetime(2024, 1, 2, 21, 0, tzinfo=UTC),
        open=Decimal(10),
        high=Decimal(11),
        low=Decimal(9),
        close=Decimal(10),
        volume=100,
    )
    second = DailyOHLCV.objects.create(
        instrument=instrument,
        trading_date=date(2024, 1, 3),
        timestamp=datetime(2024, 1, 3, 21, 0, tzinfo=UTC),
        open=Decimal(10),
        high=Decimal(12),
        low=Decimal(10),
        close=Decimal(11),
        volume=100,
    )
    points = compute_daily_returns([first, second])
    assert points[0].simple_return is None
    assert points[0].log_return is None
    assert points[1].simple_return is not None
    assert points[1].log_return is not None
    assert isclose(points[1].simple_return, 0.1, rel_tol=1e-12)
    assert isclose(points[1].log_return, log(1.1), rel_tol=1e-12)


@pytest.mark.django_db
def test_adjusted_close_preferred_when_present(instrument) -> None:
    first = DailyOHLCV.objects.create(
        instrument=instrument,
        trading_date=date(2024, 1, 2),
        timestamp=datetime(2024, 1, 2, 21, 0, tzinfo=UTC),
        open=Decimal(10),
        high=Decimal(11),
        low=Decimal(9),
        close=Decimal(10),
        adjusted_close=Decimal(20),
        volume=100,
    )
    second = DailyOHLCV.objects.create(
        instrument=instrument,
        trading_date=date(2024, 1, 3),
        timestamp=datetime(2024, 1, 3, 21, 0, tzinfo=UTC),
        open=Decimal(10),
        high=Decimal(12),
        low=Decimal(10),
        close=Decimal(11),
        adjusted_close=Decimal(22),
        volume=100,
    )
    points = compute_daily_returns([first, second])
    assert points[1].simple_return is not None
    assert isclose(points[1].simple_return, 0.1, rel_tol=1e-12)
