import uuid
from django.db import models
from django.conf import settings
from apps.instruments.models import Instrument


class Portfolio(models.Model):
    """User-owned portfolio blueprint"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="portfolios")
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
        abstract = True


class PortfolioInstrument(models.Model):
    """Explicit link table establishing a Many-to-Many relationship between Portfolios and Instruments."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    portfolio = models.ForeignKey(Portfolio, on_delete=models.CASCADE, related_name="portfolio_instruments")
    instrument = models.ForeignKey(Instrument, on_delete=models.PROTECT, related_name="portfolio_links")
    alias = models.CharField(max_length=100, null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "mda_portfolio_instruments"
        constraints = [
            models.UniqueConstraint(fields=["portfolio", "instrument"], name="unique_portfolio_instrument")
        ]
        abstract = True
