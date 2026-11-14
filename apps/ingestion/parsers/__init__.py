"""Pandas normalization and validation over staged CSV chunks."""

from .dataframe_processor import ProcessedChunk, process_staged_chunk
from .profiles import canonical_columns, default_column_map

__all__ = [
    "ProcessedChunk",
    "canonical_columns",
    "default_column_map",
    "process_staged_chunk",
]
