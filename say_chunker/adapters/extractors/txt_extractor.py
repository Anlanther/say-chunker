"""Plain-text extractor."""

from __future__ import annotations

from pathlib import Path

from say_chunker.domain.models import Section, SourceDocument
from say_chunker.utils.textutil import decode_text_bytes


class TxtExtractor:
    name = "txt"

    def extract(self, path: Path, *, password: str | None = None) -> SourceDocument:
        del password  # not applicable
        text, warnings = decode_text_bytes(path.read_bytes())
        return SourceDocument(
            title=path.stem,
            text=text,
            page_count=1,
            sections=[Section(title=None, text=text, level=0)],
            warnings=warnings,
        )
