"""Chunked CSV tick parsing using pandas' streaming reader."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import BinaryIO

import pandas as pd

from .parser_types import (
    FileParseError,
    ParsedRow,
    ParserContext,
    ParserRequest,
    build_context,
    normalize_row,
)

_MAX_CHUNK_SIZE = 10_000


@dataclass(frozen=True)
class CsvOptions:
    delimiter: str
    encoding: str
    header_row: int
    chunk_size: int


def _integer_option(options: Mapping[str, object], name: str, default: int) -> int:
    try:
        return int(str(options.get(name, default)))
    except (TypeError, ValueError) as exc:
        raise FileParseError(f"CSV option {name!r} must be an integer.") from exc


def _options(schema_mapping: Mapping[str, object]) -> CsvOptions:
    raw_options = schema_mapping.get("csv", {})
    if not isinstance(raw_options, dict):
        raise FileParseError("CSV options must be an object.")
    chunk_size = _integer_option(raw_options, "chunk_size", 1000)
    header_row = _integer_option(raw_options, "header_row", 0)
    delimiter = str(raw_options.get("delimiter", ","))
    if not 1 <= chunk_size <= _MAX_CHUNK_SIZE:
        raise FileParseError(f"CSV chunk_size must be between 1 and {_MAX_CHUNK_SIZE}.")
    if header_row < 0 or len(delimiter) != 1:
        raise FileParseError("CSV header_row or delimiter is invalid.")
    return CsvOptions(
        delimiter=delimiter,
        encoding=str(raw_options.get("encoding", "utf-8-sig")),
        header_row=header_row,
        chunk_size=chunk_size,
    )


def _chunks(stream: BinaryIO, options: CsvOptions) -> Iterator[pd.DataFrame]:
    try:
        reader = pd.read_csv(
            stream,
            sep=options.delimiter,
            encoding=options.encoding,
            header=options.header_row,
            chunksize=options.chunk_size,
            dtype=str,
            keep_default_na=False,
            na_filter=False,
            on_bad_lines="error",
        )
        yield from reader
    except FileParseError:
        raise
    except (
        pd.errors.ParserError,
        pd.errors.EmptyDataError,
        UnicodeError,
        OSError,
        ValueError,
    ) as exc:
        raise FileParseError("CSV file is empty, unreadable, or structurally invalid.") from exc


def _headings(chunk: pd.DataFrame, context: ParserContext) -> list[str]:
    names = [str(value) for value in chunk.columns]
    if len(names) != len(set(names)):
        raise FileParseError("CSV contains duplicate column names.")
    missing = sorted(set(context.columns.values()) - set(names))
    if missing:
        raise FileParseError(f"CSV is missing mapped columns: {', '.join(missing)}.")
    return names


def _chunk_records(
    chunk: pd.DataFrame, names: list[str], context: ParserContext, first_row: int
) -> Iterator[ParsedRow]:
    for offset, raw_values in enumerate(chunk.itertuples(index=False, name=None)):
        values: tuple[object, ...] = tuple(raw_values)
        row = dict(zip(names, values, strict=True))
        yield normalize_row(row, context, first_row + offset)


def iter_csv_records(request: ParserRequest) -> Iterator[ParsedRow]:
    """Yield validated CSV rows without loading the complete file into memory."""

    options = _options(request.schema_mapping)
    context = build_context(request)
    first_row = options.header_row + 2
    data_rows = 0
    for chunk in _chunks(request.stream, options):
        names = _headings(chunk, context)
        yield from _chunk_records(chunk, names, context, first_row + data_rows)
        data_rows += len(chunk)
    if data_rows == 0:
        raise FileParseError("CSV file contains no data rows.")
