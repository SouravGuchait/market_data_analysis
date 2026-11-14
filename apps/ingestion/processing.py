"""Chunk orchestration: persist batches, checkpoints, and progress."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.ingestion.converters.types import ConversionManifest, ConversionRejection
from apps.ingestion.models import (
    BatchStatus,
    DataType,
    Ingestion,
    IngestionBatch,
)
from apps.ingestion.parsers.dataframe_processor import ProcessedChunk
from apps.ingestion.persist_ohlcv import persist_ohlcv_dataframe
from apps.ingestion.persist_rejections import (
    bulk_insert_rejections,
    rejected_rows_from_conversion,
    rejected_rows_from_frame,
)
from apps.ingestion.persist_trades import persist_trade_dataframe

logger = logging.getLogger("apps.ingestion.processing")

CONVERSION_BATCH_NUMBER = 0


@dataclass(frozen=True)
class BatchCounts:
    """Counters for one completed batch."""

    rows_read: int
    accepted_rows: int
    rejected_rows: int
    duplicate_rows: int


def batch_is_completed(ingestion: Ingestion, batch_number: int) -> bool:
    return IngestionBatch.objects.filter(
        ingestion=ingestion,
        batch_number=batch_number,
        status=BatchStatus.COMPLETED,
    ).exists()


def reconcile_ingestion_counters(ingestion: Ingestion) -> None:
    """Rebuild ingestion counters from completed batch checkpoints."""

    aggregates = (
        IngestionBatch.objects.filter(ingestion=ingestion, status=BatchStatus.COMPLETED)
        .exclude(batch_number=CONVERSION_BATCH_NUMBER)
        .aggregate(
            rows_read=Sum("rows_read"),
            accepted_rows=Sum("accepted_rows"),
            rejected_rows=Sum("rejected_rows"),
            duplicate_rows=Sum("duplicate_rows"),
        )
    )
    conversion_rejected = (
        IngestionBatch.objects.filter(
            ingestion=ingestion,
            batch_number=CONVERSION_BATCH_NUMBER,
            status=BatchStatus.COMPLETED,
        )
        .values_list("rejected_rows", flat=True)
        .first()
        or 0
    )
    chunks = (
        IngestionBatch.objects.filter(ingestion=ingestion, status=BatchStatus.COMPLETED)
        .exclude(batch_number=CONVERSION_BATCH_NUMBER)
        .count()
    )
    Ingestion.objects.filter(pk=ingestion.pk).update(
        rows_read=int(aggregates["rows_read"] or 0),
        accepted_rows=int(aggregates["accepted_rows"] or 0),
        rejected_rows=int(aggregates["rejected_rows"] or 0) + int(conversion_rejected),
        duplicate_rows=int(aggregates["duplicate_rows"] or 0),
        chunks_processed=chunks,
        updated_at=timezone.now(),
    )


def _row_bounds(frame: pd.DataFrame) -> tuple[int, int]:
    if frame.empty or "_source_row" not in frame.columns:
        return 0, 0
    values = frame["_source_row"].astype(int)
    return int(values.min()), int(values.max())


def _merge_frames(*frames: pd.DataFrame) -> pd.DataFrame:
    nonempty = [frame for frame in frames if not frame.empty]
    if not nonempty:
        return pd.DataFrame()
    return pd.concat(nonempty, ignore_index=True)


def _persist_valid(frame: pd.DataFrame, ingestion: Ingestion) -> tuple[int, int, pd.DataFrame]:
    if ingestion.data_type == DataType.TICK_DATA:
        result = persist_trade_dataframe(frame, ingestion)
        return result.inserted, result.duplicates, result.conflicts
    result = persist_ohlcv_dataframe(frame)
    return result.inserted, result.duplicates, result.conflicts


def _complete_batch(
    ingestion: Ingestion,
    batch_number: int,
    counts: BatchCounts,
    *,
    start_row: int,
    end_row: int,
) -> IngestionBatch:
    batch, _created = IngestionBatch.objects.update_or_create(
        ingestion=ingestion,
        batch_number=batch_number,
        defaults={
            "source_start_row": start_row,
            "source_end_row": end_row,
            "rows_read": counts.rows_read,
            "accepted_rows": counts.accepted_rows,
            "rejected_rows": counts.rejected_rows,
            "duplicate_rows": counts.duplicate_rows,
            "status": BatchStatus.COMPLETED,
            "completed_at": timezone.now(),
        },
    )
    return batch


def persist_conversion_rejections(
    ingestion: Ingestion,
    rejections: list[ConversionRejection],
) -> None:
    """Persist Feature 006 conversion rejections as batch 0 once."""

    if batch_is_completed(ingestion, CONVERSION_BATCH_NUMBER):
        return
    with transaction.atomic():
        batch = _complete_batch(
            ingestion,
            CONVERSION_BATCH_NUMBER,
            BatchCounts(0, 0, len(rejections), 0),
            start_row=0,
            end_row=0,
        )
        rows = rejected_rows_from_conversion(rejections, ingestion=ingestion, batch=batch)
        bulk_insert_rejections(rows)
        reconcile_ingestion_counters(ingestion)


def persist_processed_chunk(
    ingestion: Ingestion,
    batch_number: int,
    chunk: ProcessedChunk,
) -> None:
    """Persist one validated chunk and write its checkpoint."""

    if batch_is_completed(ingestion, batch_number):
        logger.info(
            "Skipping already completed ingestion batch.",
            extra={"ingestion_id": str(ingestion.pk), "batch_number": batch_number},
        )
        return

    combined = _merge_frames(chunk.valid_df, chunk.rejected_df, chunk.duplicate_df)
    start_row, end_row = _row_bounds(combined)
    rows_read = len(combined)

    with transaction.atomic():
        inserted, db_duplicates, conflict_df = _persist_valid(chunk.valid_df, ingestion)
        rejected_frame = _merge_frames(chunk.rejected_df, conflict_df)
        batch = _complete_batch(
            ingestion,
            batch_number,
            BatchCounts(
                rows_read=rows_read,
                accepted_rows=inserted,
                rejected_rows=len(rejected_frame),
                duplicate_rows=len(chunk.duplicate_df) + db_duplicates,
            ),
            start_row=start_row,
            end_row=end_row,
        )
        bulk_insert_rejections(
            rejected_rows_from_frame(rejected_frame, ingestion=ingestion, batch=batch)
        )
        reconcile_ingestion_counters(ingestion)


def persist_empty_file_batch(ingestion: Ingestion, manifest: ConversionManifest) -> None:
    """Record a completed zero-row processing outcome."""

    if batch_is_completed(ingestion, 1):
        return
    with transaction.atomic():
        _complete_batch(
            ingestion,
            1,
            BatchCounts(
                rows_read=manifest.total_rows, accepted_rows=0, rejected_rows=0, duplicate_rows=0
            ),
            start_row=0,
            end_row=0,
        )
        reconcile_ingestion_counters(ingestion)
