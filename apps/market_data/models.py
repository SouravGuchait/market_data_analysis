"""Daily market data models."""

from __future__ import annotations

from django.db import models
from django.db.models import F, Q

from apps.instruments.models import Instrument


class GeneratorSymbol(models.Model):
    """Symbol/name catalog used by the deterministic data generator."""

    symbol = models.CharField(max_length=20, primary_key=True)
    name = models.CharField(max_length=128)

    class Meta:
        db_table = "mda_generator_symbols"
        ordering = ["symbol"]

    def __str__(self) -> str:
        return f"{self.symbol} - {self.name}"


class TickColumn(models.Model):
    """A named column in the generator's optional tick export."""

    name = models.CharField(max_length=32, primary_key=True)
    position = models.PositiveSmallIntegerField(unique=True)

    class Meta:
        db_table = "mda_tick_columns"
        ordering = ["position"]

    def __str__(self) -> str:
        return self.name


class AssetClass(models.Model):
    """An allowed generator asset class."""

    code = models.CharField(max_length=20, primary_key=True)
    position = models.PositiveSmallIntegerField(unique=True)

    class Meta:
        db_table = "mda_asset_classes"
        ordering = ["position", "code"]

    def __str__(self) -> str:
        return self.code


class Currency(models.Model):
    """An allowed generator currency."""

    code = models.CharField(max_length=3, primary_key=True)
    position = models.PositiveSmallIntegerField(unique=True)

    class Meta:
        db_table = "mda_currencies"
        ordering = ["position", "code"]

    def __str__(self) -> str:
        return self.code


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
    adjusted_close = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)
    volume = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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
            models.CheckConstraint(condition=Q(high__gte=F("low")), name="ohlcv_high_gte_low"),
        ]
        indexes = [models.Index(fields=["trading_date"], name="idx_ohlcv_date")]

    def __str__(self) -> str:
        return f"{self.instrument.symbol} - {self.trading_date}"
