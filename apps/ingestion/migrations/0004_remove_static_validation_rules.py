"""Drop the static validation-rule tables and the stored file-type list."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("ingestion", "0003_ensure_validation_rule_tables"),
    ]

    operations = [
        migrations.DeleteModel(name="ValidationRule"),
        migrations.DeleteModel(name="ValidationRuleSet"),
        migrations.RemoveField(model_name="ingestionrule", name="validation_rule_set_id"),
        migrations.RemoveField(model_name="ingestionrule", name="supported_formats"),
        migrations.RemoveField(model_name="ingestion", name="validation_rule_set_version"),
    ]
