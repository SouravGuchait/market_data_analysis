"""Read-only, row-streaming XLSX tick parser."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl.worksheet.worksheet import Worksheet

from .parser_types import (
    FileParseError,
    ParsedRow,
    ParserContext,
    ParserRequest,
    build_context,
    normalize_row,
)


def _xlsx_options(schema_mapping: Mapping[str, object]) -> tuple[str, int, bool]:
    options = schema_mapping.get("xlsx", {})
    if not isinstance(options, dict):
        raise FileParseError("XLSX options must be an object.")
    worksheet = options.get("worksheet")
    if not isinstance(worksheet, str) or not worksheet.strip():
        raise FileParseError("XLSX worksheet must be explicitly configured.")
    try:
        header_row = int(str(options.get("header_row", 1)))
    except (TypeError, ValueError) as exc:
        raise FileParseError("XLSX header_row must be an integer.") from exc
    use_cached = options.get("use_cached_values", True)
    if header_row < 1 or not isinstance(use_cached, bool):
        raise FileParseError("XLSX header_row or formula value policy is invalid.")
    return worksheet, header_row, use_cached


def _header_names(values: tuple[object, ...], context: ParserContext) -> list[str]:
    names = [str(value).strip() if value is not None else "" for value in values]
    if not names or any(not name for name in names) or len(names) != len(set(names)):
        raise FileParseError("XLSX contains an empty or duplicate header.")
    missing = sorted(set(context.columns.values()) - set(names))
    if missing:
        raise FileParseError(f"XLSX is missing mapped columns: {', '.join(missing)}.")
    return names


def _worksheet_rows(
    worksheet: Worksheet,
    header_row: int,
    context: ParserContext,
) -> Iterator[tuple[int, dict[str, object]]]:
    rows = worksheet.iter_rows(values_only=True)
    for _ in range(header_row - 1):
        next(rows, None)
    headings = next(rows, None)
    if headings is None:
        raise FileParseError("Configured XLSX header row is missing.")
    names = _header_names(tuple(headings), context)
    count = 0
    for row_number, values in enumerate(rows, start=header_row + 1):
        count += 1
        yield (
            row_number,
            {
                name: values[index] if index < len(values) else None
                for index, name in enumerate(names)
            },
        )
    if count == 0:
        raise FileParseError("XLSX worksheet contains no data rows.")


def _normalized_rows(
    rows: Iterator[tuple[int, dict[str, object]]], context: ParserContext
) -> Iterator[ParsedRow]:
    for row_number, row in rows:
        yield normalize_row(row, context, row_number)


def iter_xlsx_records(request: ParserRequest) -> Iterator[ParsedRow]:
    """Yield XLSX rows using openpyxl read-only mode."""

    worksheet_name, header_row, use_cached = _xlsx_options(request.schema_mapping)
    context = build_context(request)
    workbook = None
    try:
        workbook = load_workbook(request.stream, read_only=True, data_only=use_cached)
        if worksheet_name not in workbook.sheetnames:
            raise FileParseError("Configured worksheet was not found in the workbook.")
        yield from _normalized_rows(
            _worksheet_rows(workbook[worksheet_name], header_row, context), context
        )
    except FileParseError:
        raise
    except (InvalidFileException, OSError, KeyError, ValueError, BadZipFile) as exc:
        raise FileParseError("XLSX workbook is unreadable or structurally invalid.") from exc
    finally:
        if workbook is not None:
            workbook.close()
