"""Shared tradable instrument listings."""

from __future__ import annotations

from django.db import models
from apps.exchanges.models import Exchange


class Instrument(models.Model):
    """An instrument listing on one exchange."""

    class AssetClass(models.TextChoices):
        EQUITY = "equity", "Equity"
        ETF = "etf", "Exchange traded fund"
        CRYPTO = "crypto", "Cryptocurrency"

    class Currency(models.TextChoices):
        USD = "USD", "US Dollar"
        EUR = "EUR", "Euro"
        GBP = "GBP", "British Pound"
        JPY = "JPY", "Japanese Yen"
        CNY = "CNY", "Chinese Yuan"
        CHF = "CHF", "Swiss Franc"

    exchange = models.ForeignKey(
        Exchange, on_delete=models.PROTECT, related_name="instruments"
    )
    symbol = models.CharField(max_length=50)
    name = models.CharField(max_length=255)
    asset_class = models.CharField(max_length=20, choices=AssetClass.choices)
    currency = models.CharField(max_length=3, choices=Currency.choices)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "mda_instruments"
        constraints = [
            models.UniqueConstraint(
                fields=["exchange", "symbol"], name="unique_exchange_symbol"
            )
        ]
        indexes = [models.Index(fields=["symbol"], name="idx_instrument_symbol")]
        abstract = True

    def __str__(self) -> str:
        return f"{self.symbol} ({self.exchange.code})"
