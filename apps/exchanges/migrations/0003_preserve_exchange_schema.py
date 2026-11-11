"""Restore the established Exchange table and field sizes without losing rows."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("exchanges", "0002_alter_exchange_options_alter_exchange_code_and_more")]

    operations = [
        migrations.AlterModelOptions(
            name="exchange",
            options={
                "ordering": ["code"],
                "verbose_name": "Exchange",
                "verbose_name_plural": "Exchanges",
            },
        ),
        migrations.AlterField(
            model_name="exchange", name="code", field=models.CharField(max_length=32, unique=True)
        ),
        migrations.AlterField(
            model_name="exchange", name="name", field=models.CharField(max_length=128)
        ),
        migrations.AlterField(
            model_name="exchange", name="timezone", field=models.CharField(default="UTC", max_length=64)
        ),
        migrations.AlterModelTable(name="exchange", table="exchanges_exchange"),
    ]
