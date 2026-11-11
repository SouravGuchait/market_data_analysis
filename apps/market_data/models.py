"""Daily market data models."""

from __future__ import annotations

from django.db import models
from django.db.models import F, Q

from apps.instruments.models import Instrument


class DailyOHLCV(models.Model):
    """One daily OHLCV candle for an instrument."""

    instrument = models.ForeignKey(
        Instrument, on_delete=models.CASCADE, related_name="daily_candles"
    )
    trading_date = models.DateField()
    timestamp = models.DateTimeField()
    open = models.DecimalField(max_digits=20, decimal_places=6)
    high = models.DecimalField(max_digits=20, decimal_places=6)
    low = models.DecimalField(max_digits=20, decimal_places=6)
    close = models.DecimalField(max_digits=20, decimal_places=6)
    adjusted_close = models.DecimalField(
        max_digits=20, decimal_places=6, null=True, blank=True
    )
    volume = models.PositiveBigIntegerField()

    class Meta:
        db_table = "mda_daily_ohlcv"
        constraints = [
            models.UniqueConstraint(
                fields=["instrument", "trading_date"],
                name="unique_instrument_trading_date",
            ),
            models.CheckConstraint(condition=Q(open__gt=0), name="ohlcv_open_positive"),
            models.CheckConstraint(condition=Q(high__gt=0), name="ohlcv_high_positive"),
            models.CheckConstraint(condition=Q(low__gt=0), name="ohlcv_low_positive"),
            models.CheckConstraint(condition=Q(close__gt=0), name="ohlcv_close_positive"),
            models.CheckConstraint(
                condition=Q(high__gte=F("open")) & Q(high__gte=F("close")),
                name="ohlcv_high_bounds",
            ),
            models.CheckConstraint(
                condition=Q(low__lte=F("open")) & Q(low__lte=F("close")),
                name="ohlcv_low_bounds",
            ),
            models.CheckConstraint(
                condition=Q(high__gte=F("low")), name="ohlcv_high_gte_low"
            ),
        ]
        indexes = [models.Index(fields=["trading_date"], name="idx_ohlcv_date")]
        abstract = True

    def __str__(self) -> str:
        return f"{self.instrument.symbol} - {self.trading_date}"
