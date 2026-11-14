"""Per-ingestion CSV staging directories, manifests and cleanup."""

from __future__ import annotations

import json
import logging
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from apps.ingestion.converters.types import ConversionManifest
from apps.ingestion.models import Ingestion, IngestionState

logger = logging.getLogger("apps.ingestion.staging")

MANIFEST_NAME = "manifest.json"


def staging_root() -> Path:
    return Path(settings.INGESTION_LOCAL_ROOT).expanduser().resolve()


def staging_dir_for(ingestion: Ingestion) -> Path:
    """Return the isolated staging directory for one ingestion."""

    owner = ingestion.owner
    owner_segment = f"users/{owner.pk}" if owner is not None else "system"
    path = staging_root() / owner_segment / "staging" / str(ingestion.pk)
    if not path.resolve().is_relative_to(staging_root()):
        raise ValueError("Staging path escaped the configured ingestion root.")
    return path


def ensure_staging_dir(ingestion: Ingestion) -> Path:
    path = staging_dir_for(ingestion)
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_manifest(staging_dir: Path, manifest: ConversionManifest) -> Path:
    path = staging_dir / MANIFEST_NAME
    path.write_text(json.dumps(manifest.as_dict(), indent=2, sort_keys=True), encoding="utf-8")
    return path


def read_manifest(staging_dir: Path) -> dict[str, object]:
    path = staging_dir / MANIFEST_NAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Staging manifest must be a JSON object.")
    return payload


def list_chunk_paths(staging_dir: Path) -> list[Path]:
    return sorted(staging_dir.glob("chunk_*.csv"))


def delete_staging(ingestion: Ingestion) -> bool:
    """Delete staging files for one ingestion. Never touches original uploads."""

    path = staging_dir_for(ingestion)
    if not path.exists():
        return False
    shutil.rmtree(path)
    logger.info(
        "Deleted ingestion staging directory.",
        extra={"ingestion_id": str(ingestion.pk)},
    )
    return True


def _retention_seconds(state: str) -> int:
    if state == IngestionState.SUCCESS:
        return int(settings.INGESTION_STAGING_RETENTION_SUCCESS_SECONDS)
    return int(settings.INGESTION_STAGING_RETENTION_FAILED_SECONDS)


def cleanup_staging_for_ingestion(ingestion: Ingestion) -> bool:
    """Apply retention policy for one finished ingestion."""

    if ingestion.state not in {
        IngestionState.SUCCESS,
        IngestionState.FAILED,
        IngestionState.PARTIAL_SUCCESS,
        IngestionState.CANCELLED,
    }:
        return False
    finished = ingestion.finished_at or ingestion.updated_at
    age = timezone.now() - finished
    if age < timedelta(seconds=_retention_seconds(ingestion.state)):
        return False
    return delete_staging(ingestion)


def cleanup_abandoned_staging(*, older_than_seconds: int | None = None) -> int:
    """Remove staging dirs whose ingestion is missing or past retention."""

    limit = older_than_seconds
    if limit is None:
        limit = int(settings.INGESTION_STAGING_RETENTION_FAILED_SECONDS)
    root = staging_root()
    removed = 0
    for manifest_path in root.glob("**/staging/*/manifest.json"):
        removed += int(_cleanup_manifest_path(manifest_path, limit))
    return removed


def _cleanup_manifest_path(manifest_path: Path, older_than_seconds: int) -> bool:
    ingestion_id = manifest_path.parent.name
    ingestion = Ingestion.objects.filter(pk=ingestion_id).first()
    if ingestion is None:
        modified = datetime.fromtimestamp(manifest_path.stat().st_mtime, tz=UTC)
        if (timezone.now() - modified).total_seconds() < older_than_seconds:
            return False
        shutil.rmtree(manifest_path.parent, ignore_errors=True)
        return True
    return cleanup_staging_for_ingestion(ingestion)
