"""Layout-aware PDF extraction via PyMuPDF.

Handles reading order, repeated header/footer stripping, page-number removal,
hyphenated line-wrap collapsing, TOC-based section boundaries, and scanned-page
warnings. OCR is intentionally *not* bundled.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pymupdf

from say_chunker.domain.errors import ExtractionError
from say_chunker.domain.models import Section, SourceDocument

_HEADER_BAND = 0.12
_FOOTER_BAND = 0.88
_PAGE_NUMBER_RE = re.compile(r"^\d{1,4}$")


@dataclass
class _Block:
    text: str
    y0: float
    y1: float
    in_band: bool


class PDFExtractor:
    name = "pdf"

    def extract(self, path: Path, *, password: str | None = None) -> SourceDocument:
        doc = self._open(path, password)
        try:
            title = str(doc.metadata.get("title") or path.stem)
            page_count = int(doc.page_count)
            pages, heights = self._extract_pages(doc, page_count)
            header_footer = _detect_header_footer(pages)
            page_texts, blank_pages = _build_page_texts(pages, heights, header_footer)
            toc = doc.get_toc()
            sections = _build_sections(page_texts, toc, page_count)
        finally:
            doc.close()

        warnings: list[str] = []
        if blank_pages:
            warnings.append(
                f"pages {_format_page_list(blank_pages)} contain no extractable text "
                "(likely scanned images); run `ocrmypdf` on the file first"
            )

        return SourceDocument(
            title=title,
            text="\n\n".join(section.text for section in sections),
            page_count=page_count,
            sections=sections,
            warnings=warnings,
        )

    def _open(self, path: Path, password: str | None) -> Any:
        try:
            doc = pymupdf.open(str(path))
        except Exception as exc:  # noqa: BLE001 - translate to a user-facing error
            raise ExtractionError(f"could not open PDF {path}: {exc}") from exc

        if doc.needs_pass:
            if password is None:
                doc.close()
                raise ExtractionError("PDF is password-protected; pass --pdf-password")
            if not doc.authenticate(password):
                doc.close()
                raise ExtractionError("incorrect PDF password")
        return doc

    def _extract_pages(self, doc: Any, page_count: int) -> tuple[list[list[_Block]], list[float]]:
        pages: list[list[_Block]] = []
        heights: list[float] = []
        for index in range(page_count):
            page = doc[index]
            height = float(page.rect.height)
            heights.append(height)
            blocks: list[_Block] = []
            for entry in page.get_text("blocks"):
                if entry[6] != 0:  # only text blocks (0 == text, 1 == image)
                    continue
                text = str(entry[4]).strip()
                if not text:
                    continue
                y0 = float(entry[1])
                y1 = float(entry[3])
                blocks.append(
                    _Block(
                        text=text,
                        y0=y0,
                        y1=y1,
                        in_band=y1 <= _HEADER_BAND * height or y0 >= _FOOTER_BAND * height,
                    )
                )
            pages.append(blocks)
        return pages, heights


def _detect_header_footer(pages: list[list[_Block]]) -> set[str]:
    counts: dict[str, int] = {}
    band_only: dict[str, bool] = {}
    for page_blocks in pages:
        seen: set[str] = set()
        for block in page_blocks:
            key = _normalize(block.text)
            if not key:
                continue
            if key not in seen:
                counts[key] = counts.get(key, 0) + 1
                seen.add(key)
                band_only.setdefault(key, True)
            if not block.in_band:
                band_only[key] = False

    threshold = max(2, math.ceil(len(pages) * 0.5))
    return {key for key, count in counts.items() if count >= threshold and band_only[key]}


def _build_page_texts(
    pages: list[list[_Block]],
    heights: list[float],
    header_footer: set[str],
) -> tuple[list[str], list[int]]:
    page_texts: list[str] = []
    blank_pages: list[int] = []
    for index, blocks in enumerate(pages):
        parts: list[str] = []
        for block in blocks:
            if _normalize(block.text) in header_footer:
                continue
            if _is_page_number(block, heights[index]):
                continue
            cleaned = _collapse_hyphenation(block.text)
            cleaned = re.sub(r"\s*\n\s*", " ", cleaned).strip()
            if cleaned:
                parts.append(cleaned)
        text = "\n\n".join(parts)
        page_texts.append(text)
        if not text.strip():
            blank_pages.append(index + 1)
    return page_texts, blank_pages


def _is_page_number(block: _Block, height: float) -> bool:
    if not block.in_band:
        return False
    return bool(_PAGE_NUMBER_RE.match(block.text.strip()))


def _collapse_hyphenation(text: str) -> str:
    return re.sub(r"-\n(?=[a-z])", "", text)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _build_sections(page_texts: list[str], toc: list[Any], page_count: int) -> list[Section]:
    entries: list[tuple[int, str, int]] = []
    for item in toc:
        level, title, page = int(item[0]), str(item[1]), int(item[2])
        entries.append((max(1, level), title, max(1, page)))

    if not entries:
        return [Section(title=None, text="\n\n".join(page_texts), level=0)]

    sections: list[Section] = []
    first_start = entries[0][2]
    if first_start > 1:
        sections.append(
            Section(title=None, text="\n\n".join(page_texts[0 : first_start - 1]), level=0)
        )

    for idx, (level, title, start) in enumerate(entries):
        next_start = entries[idx + 1][2] if idx + 1 < len(entries) else page_count + 1
        start_index = start - 1
        end_index = next_start - 1
        text = "\n\n".join(page_texts[start_index:end_index])
        sections.append(Section(title=title or None, text=text, level=level))

    return [section for section in sections if section.text.strip()]


def _format_page_list(pages: list[int]) -> str:
    if len(pages) <= 10:
        return ", ".join(str(p) for p in pages)
    return f"{', '.join(str(p) for p in pages[:10])}, ... ({len(pages)} pages)"
