from django.db import models

class Exchange(models.Model):
    """Store shared exchange master data."""
    id = models.BigAutoField(primary_key=True)
    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=255)
    timezone = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "mda_exchanges"
        verbose_name = "Exchange"
        verbose_name_plural = "Exchanges"

    def __str__(self):
        return self.code
