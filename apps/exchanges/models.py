"""Exchange master data."""

from __future__ import annotations

from django.db import models

from apps.common.models import TimeStampedModel


class Exchange(TimeStampedModel):
    """A trading venue with its configured timezone."""

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=128)
    timezone = models.CharField(max_length=64, default="UTC")
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "exchanges_exchange"
        ordering = ["code"]

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"
