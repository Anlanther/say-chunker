"""Plain-text extractor."""

from __future__ import annotations

from pathlib import Path

from say_chunker.domain.errors import ExtractionError
from say_chunker.domain.models import Section, SourceDocument
from say_chunker.utils.textutil import decode_text, normalize_document_text


class TxtExtractor:
    name = "txt"

    def extract(self, path: Path) -> SourceDocument:
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ExtractionError(f"Could not read text file {path}: {exc}") from exc

        text, warnings = decode_text(data, source=str(path))
        normalized = normalize_document_text(text)
        sections: list[Section] = (
            [Section(title=None, text=normalized, level=0)] if normalized else []
        )

        return SourceDocument(
            title=path.stem or "audiobook",
            text=normalized,
            page_count=0,
            sections=sections,
            warnings=warnings,
        )
