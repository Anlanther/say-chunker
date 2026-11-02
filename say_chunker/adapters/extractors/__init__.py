"""Extractor registry: dispatch by (case-insensitive) file extension."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from say_chunker.adapters.extractors.markdown_extractor import MarkdownExtractor
from say_chunker.adapters.extractors.pdf_extractor import PDFExtractor
from say_chunker.adapters.extractors.txt_extractor import TxtExtractor
from say_chunker.domain.ports import TextExtractor

_EXTRACTORS: dict[str, Callable[[], TextExtractor]] = {
    "txt": lambda: TxtExtractor(),
    "md": lambda: MarkdownExtractor(),
    "pdf": lambda: PDFExtractor(),
}

SUPPORTED_FORMATS = tuple(sorted(_EXTRACTORS))


def get_extractor(fmt: str) -> TextExtractor:
    key = fmt.lower().lstrip(".")
    factory = _EXTRACTORS.get(key)
    if factory is None:
        raise ValueError(
            f"unsupported format {fmt!r}; use --format with one of: {', '.join(SUPPORTED_FORMATS)}"
        )
    return factory()


def format_for_path(path: Path) -> str:
    return path.suffix.lower().lstrip(".")
