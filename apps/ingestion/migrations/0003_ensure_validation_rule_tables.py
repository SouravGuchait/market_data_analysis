"""Ensure validation rule tables exist on databases that applied an older 0001.

``ValidationRuleSet`` / ``ValidationRule`` are already in the migration state from
``0001_initial``. Some environments applied that migration before those models
were added to the file, so the tables were never created. This operation is
idempotent and does not change Django's model state.
"""

from __future__ import annotations

from collections.abc import Sequence

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps


def _ensure_validation_tables(
    apps: StateApps,
    schema_editor: BaseDatabaseSchemaEditor,
) -> None:
    existing = set(schema_editor.connection.introspection.table_names())
    if "mda_validation_rule_sets" not in existing:
        schema_editor.create_model(apps.get_model("ingestion", "ValidationRuleSet"))
    if "mda_validation_rules" not in existing:
        schema_editor.create_model(apps.get_model("ingestion", "ValidationRule"))


def _noop_reverse(
    apps: StateApps,
    schema_editor: BaseDatabaseSchemaEditor,
) -> None:
    return None


class Migration(migrations.Migration):
    dependencies: Sequence[tuple[str, str]] = [
        ("ingestion", "0002_feature_007_persistence"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[],
            database_operations=[
                migrations.RunPython(_ensure_validation_tables, _noop_reverse),
            ],
        ),
    ]
