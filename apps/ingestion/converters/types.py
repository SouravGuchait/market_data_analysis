"""Shared types for format conversion into CSV staging chunks."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

SOURCE_ROW_COLUMN = "_source_row"


class UnsupportedFileFormatError(ValueError):
    """Raised when a file extension or content type is not supported."""


class FormatMismatchError(ValueError):
    """Raised when the extension and actual content disagree."""


class ConversionError(ValueError):
    """Raised when a source file cannot be converted as a whole."""


@dataclass(frozen=True)
class ConvertRequest:
    """Inputs for one bounded conversion into staging CSV chunks."""

    source_file: Path
    staging_dir: Path
    chunk_size: int
    parser_config: Mapping[str, object]


@dataclass(frozen=True)
class ChunkMeta:
    """One staged CSV chunk."""

    chunk_number: int
    filename: str
    row_count: int


@dataclass(frozen=True)
class ConversionRejection:
    """One recoverable source-record rejection during conversion."""

    source_row: int
    reason_code: str
    message: str
    field_name: str = ""
    stage: str = "CONVERSION"


@dataclass
class ConversionManifest:
    """Metadata describing staged CSV chunks for one ingestion."""

    ingestion_id: str
    source_filename: str
    source_format: str
    staging_format: str
    chunk_size: int
    total_chunks: int
    total_rows: int
    chunks: list[ChunkMeta] = field(default_factory=list)
    conversion_status: str = "COMPLETED"
    rejections: list[ConversionRejection] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "ingestion_id": self.ingestion_id,
            "source_filename": self.source_filename,
            "source_format": self.source_format,
            "staging_format": self.staging_format,
            "chunk_size": self.chunk_size,
            "total_chunks": self.total_chunks,
            "total_rows": self.total_rows,
            "chunks": [
                {
                    "chunk_number": chunk.chunk_number,
                    "filename": chunk.filename,
                    "row_count": chunk.row_count,
                }
                for chunk in self.chunks
            ],
            "conversion_status": self.conversion_status,
            "rejections": [
                {
                    "source_row": item.source_row,
                    "reason_code": item.reason_code,
                    "field_name": item.field_name,
                    "message": item.message,
                    "stage": item.stage,
                }
                for item in self.rejections
            ],
        }
