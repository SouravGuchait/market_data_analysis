"""Streaming format converters that produce CSV staging chunks."""

from .detect import detect_format, verify_format
from .factory import get_converter
from .types import (
    ChunkMeta,
    ConversionManifest,
    ConversionRejection,
    ConvertRequest,
    UnsupportedFileFormatError,
)

__all__ = [
    "ChunkMeta",
    "ConversionManifest",
    "ConversionRejection",
    "ConvertRequest",
    "UnsupportedFileFormatError",
    "detect_format",
    "get_converter",
    "verify_format",
]
