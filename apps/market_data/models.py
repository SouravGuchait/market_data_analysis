"""Daily market data and tick trade models."""

from __future__ import annotations

import uuid

from django.db import models
from django.db.models import F, Q

from apps.exchanges.models import Exchange
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


class Trade(models.Model):
    """One tick-level trade event persisted from tick ingestion."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    instrument = models.ForeignKey(Instrument, on_delete=models.CASCADE, related_name="trades")
    exchange = models.ForeignKey(Exchange, on_delete=models.PROTECT, related_name="trades")
    event_timestamp = models.DateTimeField()
    price = models.DecimalField(max_digits=20, decimal_places=6)
    quantity = models.DecimalField(max_digits=20, decimal_places=8)
    exchange_trade_id = models.CharField(max_length=128, blank=True, default="")
    sequence_number = models.BigIntegerField(null=True, blank=True)
    side = models.CharField(max_length=16, blank=True, default="")
    ingestion = models.ForeignKey(
        "ingestion.Ingestion",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="trades",
    )
    source_file_reference = models.CharField(max_length=512, blank=True, default="")
    ingestion_timestamp = models.DateTimeField()
    deduplication_key = models.CharField(max_length=64)
    source_metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "mda_trades"
        ordering = ["event_timestamp", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["exchange", "instrument", "deduplication_key"],
                name="unique_trade_exchange_instrument_dedupe",
            ),
            models.CheckConstraint(condition=Q(price__gt=0), name="trade_price_positive"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="trade_quantity_positive"),
        ]
        indexes = [
            models.Index(
                fields=["instrument", "event_timestamp"],
                name="idx_trade_instrument_time",
            ),
            models.Index(
                fields=["exchange", "event_timestamp"],
                name="idx_trade_exchange_time",
            ),
            models.Index(fields=["ingestion"], name="idx_trade_ingestion"),
            models.Index(fields=["deduplication_key"], name="idx_trade_dedupe_key"),
        ]

    def __str__(self) -> str:
        return f"{self.instrument.symbol}@{self.event_timestamp}"
