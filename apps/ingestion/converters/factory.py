"""Select and run the converter for a verified source file."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .csv_converter import convert_csv
from .detect import detect_format, normalize_extension, verify_format
from .json_converter import convert_json
from .jsonl_converter import convert_jsonl
from .types import (
    ChunkMeta,
    ConversionManifest,
    ConversionRejection,
    ConvertRequest,
    UnsupportedFileFormatError,
)
from .xlsx_converter import convert_xlsx

ConverterFn = Callable[[ConvertRequest], tuple[list[ChunkMeta], int, list[ConversionRejection]]]


def _wrap_simple(
    fn: Callable[[ConvertRequest], tuple[list[ChunkMeta], int]],
) -> ConverterFn:
    def _runner(request: ConvertRequest) -> tuple[list[ChunkMeta], int, list[ConversionRejection]]:
        chunks, total = fn(request)
        return chunks, total, []

    return _runner


_CONVERTERS: dict[str, ConverterFn] = {
    ".csv": _wrap_simple(convert_csv),
    ".json": _wrap_simple(convert_json),
    ".jsonl": convert_jsonl,
    ".xlsx": _wrap_simple(convert_xlsx),
}


def get_converter(file_extension: str) -> ConverterFn:
    """Return the converter callable for a lowercase extension."""

    extension = file_extension.lower()
    if not extension.startswith("."):
        extension = f".{extension}"
    converter = _CONVERTERS.get(extension)
    if converter is None:
        raise UnsupportedFileFormatError(extension)
    return converter


def convert_source(
    request: ConvertRequest,
    *,
    ingestion_id: str,
) -> ConversionManifest:
    """Verify format, convert into staging chunks, and return a manifest."""

    extension = normalize_extension(request.source_file)
    source_format = detect_format(request.source_file)
    verify_format(request.source_file, source_format)
    converter = get_converter(extension)
    chunks, total_rows, rejections = converter(request)
    return ConversionManifest(
        ingestion_id=ingestion_id,
        source_filename=Path(request.source_file).name,
        source_format=source_format,
        staging_format="CSV",
        chunk_size=request.chunk_size,
        total_chunks=len(chunks),
        total_rows=total_rows,
        chunks=chunks,
        conversion_status="COMPLETED",
        rejections=rejections,
    )
