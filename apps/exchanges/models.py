from django.db import models


class Exchange(models.Model):
    """Store shared exchange master data."""

    id = models.BigAutoField(primary_key=True)
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=128)
    timezone = models.CharField(max_length=64, default="UTC")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "exchanges_exchange"
        ordering = ["code"]
        verbose_name = "Exchange"
        verbose_name_plural = "Exchanges"

    def __str__(self):
        return self.code
