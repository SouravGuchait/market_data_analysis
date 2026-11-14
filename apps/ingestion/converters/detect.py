"""Verify file extensions and sniff actual content shape."""

from __future__ import annotations

from pathlib import Path

from .types import FormatMismatchError, UnsupportedFileFormatError

_EXTENSION_MAP = {
    ".csv": "CSV",
    ".json": "JSON",
    ".jsonl": "JSONL",
    ".xlsx": "XLSX",
}


def normalize_extension(path: Path | str) -> str:
    """Return a lowercase extension including the leading dot."""

    suffix = Path(path).suffix.lower()
    if not suffix:
        raise UnsupportedFileFormatError("File has no extension.")
    return suffix


def detect_format(path: Path) -> str:
    """Return the canonical format label from the file extension."""

    extension = normalize_extension(path)
    label = _EXTENSION_MAP.get(extension)
    if label is None:
        raise UnsupportedFileFormatError(extension)
    return label


def _looks_like_xlsx(path: Path) -> bool:
    with path.open("rb") as handle:
        header = handle.read(4)
    return header == b"PK\x03\x04"


_JSON_ARRAY_START = ord("[")


def _looks_like_json_array(path: Path) -> bool:
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(4096)
            if not chunk:
                return False
            for byte in chunk:
                if byte in {9, 10, 13, 32}:
                    continue
                return byte == _JSON_ARRAY_START


def _looks_like_jsonl(path: Path) -> bool:
    with path.open("rb") as handle:
        sample = handle.read(8192)
    if not sample.strip():
        return False
    first = sample.lstrip()[:1]
    return first in {b"{", b"["}


def _looks_like_csv(path: Path) -> bool:
    with path.open("rb") as handle:
        sample = handle.read(4096)
    if not sample:
        return False
    if sample[:1] in {b"{", b"["} or sample[:4] == b"PK\x03\x04":
        return False
    text = sample.decode("utf-8", errors="ignore")
    return "," in text or ";" in text or "\t" in text


def verify_format(path: Path, expected: str) -> None:
    """Reject files whose content does not match the declared format."""

    checks = {
        "CSV": _looks_like_csv,
        "JSON": _looks_like_json_array,
        "JSONL": _looks_like_jsonl,
        "XLSX": _looks_like_xlsx,
    }
    checker = checks.get(expected)
    if checker is None:
        raise UnsupportedFileFormatError(expected)
    if not checker(path):
        raise FormatMismatchError(f"File content does not match expected format {expected}.")
