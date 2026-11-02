"""Layout-aware PDF extraction with repeated header/footer removal and TOC sections."""

from __future__ import annotations

import math
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pymupdf

from say_chunker.domain.errors import (
    ExtractionError,
    PDFPasswordError,
    PDFPasswordRequiredError,
)
from say_chunker.domain.models import Section, SourceDocument
from say_chunker.utils.textutil import normalize_paragraph

_HYPHENS = ("-", "‐", "‑")
_TERMINAL_RE = re.compile(r'[.!?…。！？:;"”’)]\s*$')
_PAGE_NUMBER_RE = re.compile(
    r"^(?:page\s*)?[·•\-–—\s]*\d{1,4}[·•\-–—\s]*(?:(?:of|/)\s*\d{1,4})?$",
    re.IGNORECASE,
)
_ROMAN_PAGE_RE = re.compile(r"^(?:page\s*)?[ivxlcdm]{1,7}$", re.IGNORECASE)
_TITLE_PUNCT_RE = re.compile(r"[^\w\s]")
_TITLE_NUMBER_RE = re.compile(r"^(?:(?:chapter|section|part)\s+)?\d+\s+", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class _PageLine:
    page_index: int
    block_index: int
    line_index: int
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    page_height: float
    page_width: float


@dataclass(frozen=True, slots=True)
class _Paragraph:
    text: str
    page_index: int
    y_top: float
    y_bottom: float
    block_index: int
    order: int


@dataclass(frozen=True, slots=True)
class _TocEntry:
    title: str
    page: int
    level: int
    dest_y: float | None


class PdfExtractor:
    name = "pdf"

    def __init__(self, *, password: str | None = None) -> None:
        self._password = password

    def extract(self, path: Path) -> SourceDocument:
        try:
            document = pymupdf.open(path)
        except Exception as exc:  # pymupdf raises several concrete exception types
            raise ExtractionError(f"Could not open PDF {path}: {exc}") from exc

        try:
            if document.needs_pass:
                if self._password is None:
                    raise PDFPasswordRequiredError(
                        f"PDF is password-protected: {path}. "
                        "Pass --pdf-password PASSWORD or enter it when prompted."
                    )
                if not document.authenticate(self._password):
                    raise PDFPasswordError(f"The supplied password was rejected for {path}.")
            try:
                return self._extract_document(document, path)
            except ExtractionError:
                raise
            except Exception as exc:
                raise ExtractionError(f"Could not extract text from PDF {path}: {exc}") from exc
        finally:
            document.close()

    def _extract_document(self, document: Any, path: Path) -> SourceDocument:
        page_count = int(document.page_count)
        if page_count <= 0:
            raise ExtractionError(f"PDF has no pages: {path}")

        lines_by_page = [
            _page_lines(document.load_page(index), index) for index in range(page_count)
        ]
        removed = _detect_removed_lines(lines_by_page, page_count)

        paragraphs: list[_Paragraph] = []
        empty_pages: list[int] = []
        for page_index, page_lines in enumerate(lines_by_page):
            kept = [
                line
                for line in page_lines
                if (line.page_index, line.block_index, line.line_index) not in removed
            ]
            if sum(len(line.text) for line in kept) < 5:
                empty_pages.append(page_index)
            paragraphs.extend(_page_paragraphs(kept, page_index))

        paragraphs = _merge_page_continuations(paragraphs)

        warnings: list[str] = []
        if empty_pages:
            page_list = ", ".join(str(page + 1) for page in empty_pages)
            message = (
                f"No extractable text on page(s) {page_list}; this PDF may contain "
                "scanned images. Run `ocrmypdf input.pdf output.pdf` first, then "
                "convert the OCR'd PDF. OCR is not bundled with this tool."
            )
            if len(empty_pages) == page_count:
                raise ExtractionError(f"{path}: {message}")
            warnings.append(message)

        toc, toc_warning = _read_toc(document, page_count)
        if toc_warning:
            warnings.append(toc_warning)

        sections = _build_sections(paragraphs, toc)
        metadata_title = _metadata_title(document)
        title = metadata_title or path.stem or "audiobook"
        full_text = "\n\n".join(section.text for section in sections if section.text)

        return SourceDocument(
            title=title,
            text=full_text,
            page_count=page_count,
            sections=sections,
            warnings=warnings,
        )


def _page_lines(page: Any, page_index: int) -> list[_PageLine]:
    rect = page.rect
    page_height = float(rect.height)
    page_width = float(rect.width)
    data = page.get_text("dict", sort=True)
    lines: list[_PageLine] = []

    blocks = data.get("blocks", []) if isinstance(data, dict) else []
    for block_index, block in enumerate(blocks):
        if not isinstance(block, dict) or block.get("type", 0) != 0:
            continue
        for line_index, line in enumerate(block.get("lines", [])):
            if not isinstance(line, dict):
                continue
            spans = line.get("spans", [])
            text = "".join(str(span.get("text", "")) for span in spans if isinstance(span, dict))
            text = normalize_paragraph(text)
            if not text:
                continue
            bbox = line.get("bbox")
            if not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
                continue
            x0, y0, x1, y1 = (float(value) for value in bbox[:4])
            lines.append(
                _PageLine(
                    page_index=page_index,
                    block_index=block_index,
                    line_index=line_index,
                    text=text,
                    x0=x0,
                    y0=y0,
                    x1=x1,
                    y1=y1,
                    page_height=page_height,
                    page_width=page_width,
                )
            )

    return lines


def _detect_removed_lines(
    lines_by_page: list[list[_PageLine]], page_count: int
) -> set[tuple[int, int, int]]:
    edge_lines = [line for page in lines_by_page for line in page if _is_edge(line)]
    counts: Counter[str] = Counter()
    pages_by_key: dict[str, set[int]] = defaultdict(set)

    for line in edge_lines:
        key = _repeat_key(line.text)
        counts[key] += 1
        pages_by_key[key].add(line.page_index)

    threshold = 2 if page_count <= 3 else max(3, math.ceil(page_count * 0.3))

    repeated = {
        key for key, count in counts.items() if count >= threshold and len(pages_by_key[key]) >= 2
    }

    removed: set[tuple[int, int, int]] = set()
    for line in edge_lines:
        is_page_number = bool(_PAGE_NUMBER_RE.match(line.text))
        is_roman_page_number = bool(_ROMAN_PAGE_RE.match(line.text)) and (
            line.y0 >= line.page_height * 0.90 or line.y1 <= line.page_height * 0.05
        )
        if _repeat_key(line.text) in repeated or is_page_number or is_roman_page_number:
            removed.add((line.page_index, line.block_index, line.line_index))
    return removed


def _is_edge(line: _PageLine) -> bool:
    return line.y0 <= line.page_height * 0.12 or line.y1 >= line.page_height * 0.88


def _repeat_key(text: str) -> str:
    lowered = normalize_paragraph(text).casefold()
    return re.sub(r"\d+", "#", lowered)


def _page_paragraphs(lines: list[_PageLine], page_index: int) -> list[_Paragraph]:
    by_block: dict[int, list[_PageLine]] = defaultdict(list)
    for line in lines:
        by_block[line.block_index].append(line)

    paragraphs: list[_Paragraph] = []
    order = 0
    for block_lines in by_block.values():
        ordered = sorted(block_lines, key=lambda line: (line.y0, line.x0))
        for run in _split_block_into_paragraphs(ordered):
            text = _join_lines(run)
            if not text:
                continue
            paragraphs.append(
                _Paragraph(
                    text=text,
                    page_index=page_index,
                    y_top=run[0].y0,
                    y_bottom=run[-1].y1,
                    block_index=run[0].block_index,
                    order=order,
                )
            )
            order += 1
    return paragraphs


def _split_block_into_paragraphs(lines: list[_PageLine]) -> list[list[_PageLine]]:
    if not lines:
        return []
    heights = [line.y1 - line.y0 for line in lines if line.y1 > line.y0]
    median_height = statistics.median(heights) if heights else 12.0
    gap_threshold = max(3.5, median_height * 0.6)

    runs: list[list[_PageLine]] = [[lines[0]]]
    for line in lines[1:]:
        previous = runs[-1][-1]
        if line.y0 - previous.y1 > gap_threshold:
            runs.append([line])
        else:
            runs[-1].append(line)
    return runs


def _join_lines(lines: list[_PageLine]) -> str:
    result = ""
    for line in lines:
        text = line.text.strip()
        if not text:
            continue
        if not result:
            result = text
        elif result.endswith(_HYPHENS) and text[:1].islower():
            result = result[:-1] + text
        else:
            result += " " + text
    return normalize_paragraph(result)


def _merge_page_continuations(paragraphs: list[_Paragraph]) -> list[_Paragraph]:
    merged: list[_Paragraph] = []
    for paragraph in paragraphs:
        if (
            merged
            and paragraph.page_index > merged[-1].page_index
            and _is_continuation(merged[-1].text, paragraph.text)
        ):
            previous = merged[-1]
            merged[-1] = replace(
                previous,
                text=_join_text(previous.text, paragraph.text),
                y_bottom=paragraph.y_bottom,
            )
        else:
            merged.append(paragraph)
    return merged


def _is_continuation(previous: str, following: str) -> bool:
    if not following or not following[:1].islower():
        return False
    return _TERMINAL_RE.search(previous) is None


def _join_text(previous: str, following: str) -> str:
    if previous.endswith(_HYPHENS) and following[:1].islower():
        return previous[:-1] + following
    return previous + " " + following


def _read_toc(document: Any, page_count: int) -> tuple[list[_TocEntry], str | None]:
    try:
        raw_entries = document.get_toc(simple=False)
    except Exception as exc:
        return [], f"Could not read the PDF table of contents: {exc}"

    entries: list[_TocEntry] = []
    for raw in raw_entries or []:
        if not isinstance(raw, (list, tuple)) or len(raw) < 3:
            continue
        level, title, page = raw[0], raw[1], raw[2]
        if not isinstance(level, int) or not isinstance(title, str) or not isinstance(page, int):
            continue
        if page < 1 or page > page_count or not title.strip():
            continue
        dest_y: float | None = None
        if len(raw) > 3 and isinstance(raw[3], dict):
            dest_y = _destination_y(raw[3])
        entries.append(
            _TocEntry(
                title=normalize_paragraph(title),
                page=page,
                level=level,
                dest_y=dest_y,
            )
        )
    return entries, None


def _destination_y(destination: dict[str, Any]) -> float | None:
    point = destination.get("to")
    if isinstance(point, (list, tuple)) and len(point) >= 2:
        value = point[1]
        if isinstance(value, (int, float)):
            return float(value)
    if point is not None and hasattr(point, "y"):
        value = point.y
        if isinstance(value, (int, float)):
            return float(value)
    value = destination.get("y")
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _build_sections(paragraphs: list[_Paragraph], toc: list[_TocEntry]) -> list[Section]:
    if not paragraphs:
        return []

    if not toc:
        return [Section(title=None, text=_join_paragraphs(paragraphs), level=0)]

    page_first: dict[int, int] = {}
    for index, paragraph in enumerate(paragraphs):
        page_first.setdefault(paragraph.page_index, index)

    boundaries: list[tuple[_TocEntry, int]] = []
    search_from = 0
    for entry in toc:
        start = _find_section_start(paragraphs, entry, page_first, search_from)
        start = max(start, search_from)
        boundaries.append((entry, start))
        search_from = start

    sections: list[Section] = []
    first_start = boundaries[0][1]
    if first_start > 0:
        front_text = _join_paragraphs(paragraphs[:first_start])
        if front_text:
            sections.append(Section(title=None, text=front_text, level=0))

    for index, (entry, start) in enumerate(boundaries):
        end = boundaries[index + 1][1] if index + 1 < len(boundaries) else len(paragraphs)
        if end < start:
            end = start
        text = _join_paragraphs(paragraphs[start:end])
        if text:
            sections.append(Section(title=entry.title, text=text, level=entry.level))

    if not sections:
        text = _join_paragraphs(paragraphs)
        if text:
            sections.append(Section(title=None, text=text, level=0))
    return sections


def _find_section_start(
    paragraphs: list[_Paragraph],
    entry: _TocEntry,
    page_first: dict[int, int],
    search_from: int,
) -> int:
    target_page = entry.page - 1
    start = page_first.get(target_page)
    if start is None:
        start = next(
            (
                index
                for index, paragraph in enumerate(paragraphs)
                if paragraph.page_index > target_page
            ),
            len(paragraphs),
        )
    start = max(start, search_from)

    title_key = _normalize_title(entry.title)
    if title_key:
        index = start
        while index < len(paragraphs) and paragraphs[index].page_index == target_page:
            if _title_matches(paragraphs[index].text, title_key):
                return index
            index += 1

    if entry.dest_y is not None:
        for index in range(start, len(paragraphs)):
            paragraph = paragraphs[index]
            if paragraph.page_index > target_page:
                break
            if paragraph.page_index == target_page and paragraph.y_top >= entry.dest_y - 2:
                return index

    return start


def _normalize_title(text: str) -> str:
    lowered = normalize_paragraph(text).casefold()
    lowered = _TITLE_PUNCT_RE.sub(" ", lowered)
    lowered = _TITLE_NUMBER_RE.sub("", lowered)
    return _WHITESPACE_RE.sub(" ", lowered).strip()


def _title_matches(paragraph_text: str, title_key: str) -> bool:
    paragraph_key = _normalize_title(paragraph_text)
    if not paragraph_key:
        return False
    return title_key in paragraph_key or paragraph_key in title_key


def _join_paragraphs(paragraphs: list[_Paragraph]) -> str:
    return "\n\n".join(paragraph.text for paragraph in paragraphs if paragraph.text)


def _metadata_title(document: Any) -> str:
    metadata = document.metadata
    if not isinstance(metadata, dict):
        return ""
    title = metadata.get("title")
    if not isinstance(title, str):
        return ""
    return normalize_paragraph(title)
