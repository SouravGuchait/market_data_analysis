import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.exchanges.models import Exchange
from apps.instruments.models import Instrument


class Portfolio(models.Model):
    """User-owned portfolio blueprint"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="portfolios"
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=50, default="active")
    instruments = models.ManyToManyField(
        Instrument, through="PortfolioInstrument", related_name="portfolios"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "mda_portfolios"

    def __str__(self) -> str:
        return self.name


class PortfolioInstrument(models.Model):
    """Explicit link table establishing a Many-to-Many relationship between Portfolios and Instruments."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    portfolio = models.ForeignKey(
        Portfolio, on_delete=models.CASCADE, related_name="portfolio_instruments"
    )
    instrument = models.ForeignKey(
        Instrument, on_delete=models.PROTECT, related_name="portfolio_links"
    )
    exchange = models.ForeignKey(
        Exchange,
        on_delete=models.PROTECT,
        related_name="portfolio_links",
        help_text="Must match the exchange of the selected instrument.",
    )
    alias = models.CharField(max_length=100, blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "mda_portfolio_instruments"
        constraints = [
            models.UniqueConstraint(
                fields=["portfolio", "instrument"], name="unique_portfolio_instrument"
            )
        ]

    def __str__(self) -> str:
        return f"{self.portfolio.name}: {self.instrument.symbol}"

    def save(self, *args, **kwargs) -> None:
        # Legacy rows created before Feature 003 have no exchange value.
        if self.exchange_id is None:  # pyright: ignore[reportAttributeAccessIssue]
            self.exchange = self.instrument.exchange
        self._validate_exchange_matches_instrument()
        super().save(*args, **kwargs)

    def clean(self) -> None:
        self._validate_exchange_matches_instrument()

    def _validate_exchange_matches_instrument(self) -> None:
        exchange_id = self.exchange_id  # pyright: ignore[reportAttributeAccessIssue]
        if exchange_id is not None and exchange_id != self.instrument.exchange.pk:
            raise ValidationError(
                {"exchange": "The selected exchange must match the instrument's exchange."}
            )
