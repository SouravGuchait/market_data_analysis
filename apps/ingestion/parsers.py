"""Public parser entry points for individual ingestion files."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import PurePosixPath

from .csv_parser import iter_csv_records
from .json_parser import iter_json_records
from .parser_types import FileParseError, ParsedRow, ParserRequest
from .xlsx_parser import iter_xlsx_records

_MAX_BATCH_SIZE = 10_000


def iter_file_records(request: ParserRequest) -> Iterator[ParsedRow]:
    """Select a parser by extension and yield normalized row outcomes."""

    suffix = PurePosixPath(request.file_name).suffix.lower()
    parser = {
        ".csv": iter_csv_records,
        ".json": iter_json_records,
        ".xlsx": iter_xlsx_records,
    }.get(suffix)
    if parser is None:
        raise FileParseError("Unsupported file extension.")
    yield from parser(request)


def iter_file_batches(request: ParserRequest, batch_size: int = 1000) -> Iterator[list[ParsedRow]]:
    """Group parser outcomes into bounded batches for downstream persistence."""

    if not 1 <= batch_size <= _MAX_BATCH_SIZE:
        raise FileParseError(f"batch_size must be between 1 and {_MAX_BATCH_SIZE}.")
    batch: list[ParsedRow] = []
    for result in iter_file_records(request):
        batch.append(result)
        if len(batch) == batch_size:
            yield batch
            batch = []
    if batch:
        yield batch
