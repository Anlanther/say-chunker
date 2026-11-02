"""Extractor registry: extension/format dispatch for .txt, .md, and .pdf."""

from __future__ import annotations

from pathlib import Path

from say_chunker.adapters.extractors.markdown_extractor import MarkdownExtractor
from say_chunker.adapters.extractors.pdf_extractor import PdfExtractor
from say_chunker.adapters.extractors.txt_extractor import TxtExtractor
from say_chunker.domain.errors import UnsupportedFormatError
from say_chunker.domain.ports import TextExtractor

SUPPORTED_FORMATS = ("txt", "md", "pdf")
_EXTENSION_TO_FORMAT = {
    ".txt": "txt",
    ".md": "md",
    ".markdown": "md",
    ".pdf": "pdf",
}


def format_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    format_name = _EXTENSION_TO_FORMAT.get(suffix)
    if format_name is None:
        shown = suffix or "(no extension)"
        raise UnsupportedFormatError(
            f"Unsupported file type {shown!r} for {path}. "
            f"Use --format {{{','.join(SUPPORTED_FORMATS)}}} to override."
        )
    return format_name


def get_extractor(format_name: str, *, password: str | None = None) -> TextExtractor:
    key = format_name.lower().lstrip(".")
    if key in {"txt", "text"}:
        return TxtExtractor()
    if key in {"md", "markdown"}:
        return MarkdownExtractor()
    if key == "pdf":
        return PdfExtractor(password=password)
    raise UnsupportedFormatError(
        f"Unsupported format {format_name!r}. Choose one of: {', '.join(SUPPORTED_FORMATS)}."
    )


def get_extractor_for_path(
    path: Path,
    *,
    format_override: str | None = None,
    password: str | None = None,
) -> tuple[TextExtractor, str]:
    format_name = format_override or format_for_path(path)
    return get_extractor(format_name, password=password), format_name


__all__ = [
    "SUPPORTED_FORMATS",
    "MarkdownExtractor",
    "PdfExtractor",
    "TxtExtractor",
    "format_for_path",
    "get_extractor",
    "get_extractor_for_path",
]
