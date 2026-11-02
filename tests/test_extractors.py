"""Tests for the txt / markdown / pdf extractors."""

from __future__ import annotations

from pathlib import Path

from say_chunker.adapters.extractors.markdown_extractor import MarkdownExtractor
from say_chunker.adapters.extractors.pdf_extractor import (
    PDFExtractor,
    _Block,
    _detect_header_footer,
    _is_page_number,
)
from say_chunker.adapters.extractors.txt_extractor import TxtExtractor

FIXTURES = Path(__file__).parent / "fixtures"


def test_txt_extractor() -> None:
    doc = TxtExtractor().extract(FIXTURES / "sample.txt")
    assert doc.title == "sample"
    assert doc.page_count == 1
    assert len(doc.sections) == 1
    assert len(doc.text) > 10000


def test_markdown_extractor() -> None:
    doc = MarkdownExtractor().extract(FIXTURES / "sample.md")
    assert doc.title == "The Quiet Coast"
    assert len(doc.sections) == 4
    assert [section.title for section in doc.sections] == [
        "Chapter One: Arrival",
        "Chapter Two: The Harbor",
        "The Lighthouse",
        "Chapter Three: Departure",
    ]
    assert "##" not in doc.text
    assert "- " not in doc.text


def test_pdf_extractor() -> None:
    doc = PDFExtractor().extract(FIXTURES / "sample.pdf")
    assert doc.title == "My Book"
    assert doc.page_count == 4
    assert len(doc.sections) == 3
    assert [section.title for section in doc.sections] == ["Chapter 1", "Chapter 2", "Chapter 3"]
    assert "My Book" not in doc.text  # repeated header stripped


def test_is_page_number() -> None:
    assert _is_page_number(_Block("3", 820, 830, True), 842)
    assert not _is_page_number(_Block("Chapter 3", 820, 830, True), 842)
    assert not _is_page_number(_Block("3", 400, 410, False), 842)


def test_detect_header_footer() -> None:
    pages = [
        [_Block("My Book", 30, 40, True), _Block("Body one.", 100, 120, False)],
        [_Block("My Book", 30, 40, True), _Block("Body two.", 100, 120, False)],
        [_Block("My Book", 30, 40, True), _Block("Body three.", 100, 120, False)],
    ]
    assert _detect_header_footer(pages) == {"my book"}
