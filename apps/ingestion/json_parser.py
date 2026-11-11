"""Bounded JSON array and JSON Lines tick parsing."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from typing import BinaryIO

import ijson

from .parser_types import (
    FileParseError,
    ParsedRow,
    ParserContext,
    ParserRequest,
    build_context,
    normalize_row,
    reject,
)


def _json_options(schema_mapping: Mapping[str, object]) -> tuple[str, str]:
    options = schema_mapping.get("json", {})
    if not isinstance(options, dict):
        raise FileParseError("JSON options must be an object.")
    mode = options.get("mode")
    if not isinstance(mode, str) or mode not in {"array", "jsonl"}:
        raise FileParseError("JSON mode must be explicitly configured as 'array' or 'jsonl'.")
    path = options.get("records_path", "item")
    if not isinstance(path, str) or not path:
        raise FileParseError("JSON records_path must be a non-empty string.")
    return str(mode), path


def _require_array_root(stream: BinaryIO) -> None:
    start = stream.tell()
    while character := stream.read(1):
        if character.isspace():
            continue
        stream.seek(start)
        if character != b"[":
            raise FileParseError("JSON array mode requires a root array.")
        return
    stream.seek(start)
    raise FileParseError("JSON array file is empty.")


def _normalize_objects(records: Iterable[object], context: ParserContext) -> Iterator[ParsedRow]:
    count = 0
    for row_number, value in enumerate(records, start=1):
        count += 1
        if not isinstance(value, dict):
            yield reject(
                context.ingestion_id, row_number, "invalid_record", "JSON record must be an object."
            )
            continue
        row = {str(key): item for key, item in value.items()}
        yield normalize_row(row, context, row_number)
    if count == 0:
        raise FileParseError("JSON file contains no records.")


def _array_records(stream: BinaryIO, path: str, context: ParserContext) -> Iterator[ParsedRow]:
    try:
        _require_array_root(stream)
        yield from _normalize_objects(ijson.items(stream, path), context)
    except FileParseError:
        raise
    except (ijson.JSONError, OSError, UnicodeError, ValueError) as exc:
        raise FileParseError("JSON file is unreadable or has an invalid structure.") from exc


def _jsonl_rows(stream: BinaryIO, context: ParserContext) -> Iterator[ParsedRow]:
    count = 0
    for row_number, line in enumerate(stream, start=1):
        if not line.strip():
            continue
        count += 1
        try:
            value: object = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            yield reject(
                context.ingestion_id, row_number, "invalid_record", "JSON line is malformed."
            )
            continue
        if not isinstance(value, dict):
            yield reject(
                context.ingestion_id, row_number, "invalid_record", "JSON record must be an object."
            )
            continue
        row = {str(key): item for key, item in value.items()}
        yield normalize_row(row, context, row_number)
    if count == 0:
        raise FileParseError("JSON file contains no records.")


def iter_json_records(request: ParserRequest) -> Iterator[ParsedRow]:
    """Yield JSON records from the explicitly configured structure."""

    mode, path = _json_options(request.schema_mapping)
    context = build_context(request)
    if mode == "array":
        yield from _array_records(request.stream, path, context)
        return
    yield from _jsonl_rows(request.stream, context)
