from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from say_chunker.adapters.extractors import (
    MarkdownExtractor,
    PdfExtractor,
    TxtExtractor,
    format_for_path,
    get_extractor,
)
from say_chunker.domain.errors import (
    ExtractionError,
    PDFPasswordError,
    PDFPasswordRequiredError,
    UnsupportedFormatError,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_txt_extractor_long_sample() -> None:
    document = TxtExtractor().extract(FIXTURES / "long_sample.txt")
    assert document.title == "long_sample"
    assert document.page_count == 0
    assert len(document.text) > 12_000
    assert len(document.sections) == 1
    assert document.sections[0].title is None
    assert document.sections[0].level == 0
    assert "\n\n" in document.text
    assert document.text.count("Paragraph ") >= 20
    assert document.warnings == []


def test_txt_extractor_encoding_fallback(tmp_path: Path) -> None:
    path = tmp_path / "latin.txt"
    path.write_bytes(b"Caf\xe9 au lait. It costs 3.50.\n")
    document = TxtExtractor().extract(path)
    assert "Café" in document.text
    assert document.warnings
    assert "cp1252" in document.warnings[0]


def test_txt_extractor_empty(tmp_path: Path) -> None:
    path = tmp_path / "empty.txt"
    path.write_text("   \n\n  ", encoding="utf-8")
    document = TxtExtractor().extract(path)
    assert document.text == ""
    assert document.sections == []


def test_markdown_extractor_structure_and_stripping() -> None:
    document = MarkdownExtractor().extract(FIXTURES / "sample.md")
    assert document.title == "The Markdown Sample"
    assert document.page_count == 0
    assert [section.title for section in document.sections] == [
        "The Markdown Sample",
        "First Section",
        "Second Section",
    ]
    assert [section.level for section in document.sections] == [1, 2, 2]
    text = document.text
    assert "front matter" not in text
    assert "**" not in text
    assert "*italic text*" not in text
    assert "](https://example.com" not in text
    assert "a link" in text
    assert "a small diagram" in text
    assert "inline code" in text
    assert 'print("hello from a code block")' in text
    assert "A block quote should still be read aloud." in text
    assert "The Markdown Sample" in document.sections[0].text
    assert "First Section" in document.sections[1].text


def test_markdown_extractor_without_headings(tmp_path: Path) -> None:
    path = tmp_path / "notes.md"
    path.write_text("Just a paragraph. And another sentence.", encoding="utf-8")
    document = MarkdownExtractor().extract(path)
    assert document.title == "notes"
    assert len(document.sections) == 1
    assert document.sections[0].title is None
    assert document.sections[0].text == "Just a paragraph. And another sentence."


def test_markdown_extractor_empty(tmp_path: Path) -> None:
    path = tmp_path / "empty.md"
    path.write_text("", encoding="utf-8")
    document = MarkdownExtractor().extract(path)
    assert document.sections == []
    assert document.title == "empty"


def test_markdown_extractor_strips_crlf_front_matter(tmp_path: Path) -> None:
    path = tmp_path / "crlf.md"
    path.write_bytes(b"---\r\ntitle: ignore me\r\n---\r\n\r\n# Real Title\r\n\r\nBody.\r\n")
    document = MarkdownExtractor().extract(path)
    assert document.title == "Real Title"
    assert "ignore me" not in document.text
    assert document.sections[0].text.startswith("Real Title")


def test_pdf_extractor_toc_headers_footers_and_hyphenation() -> None:
    document = PdfExtractor().extract(FIXTURES / "sample.pdf")
    assert document.title == "The Great Test Book"
    assert document.page_count == 6
    assert [section.title for section in document.sections] == ["Chapter One", "Chapter Two"]
    assert [section.level for section in document.sections] == [1, 1]
    assert document.warnings == []

    text = document.text
    assert "The Great Test Book" not in text
    assert "Page 1" not in text
    assert not any(line.strip().isdigit() for line in text.splitlines())
    assert "example was difficult" in text
    assert "exam-\nple" not in text
    assert "\n\n" in text
    assert "village of Ashford" in document.sections[0].text
    assert "By noon" not in document.sections[0].text
    assert "village of Ashford" not in document.sections[1].text
    assert "By noon" in document.sections[1].text


def test_pdf_extractor_password_required(tmp_path: Path) -> None:
    path = tmp_path / "locked.pdf"
    _write_small_pdf(path, "Secret text. Second sentence.", password="secret")

    with pytest.raises(PDFPasswordRequiredError, match="password-protected"):
        PdfExtractor().extract(path)

    with pytest.raises(PDFPasswordError, match="rejected"):
        PdfExtractor(password="wrong").extract(path)

    document = PdfExtractor(password="secret").extract(path)
    assert "Secret text." in document.text


def test_pdf_extractor_warns_for_scanned_pages(tmp_path: Path) -> None:
    path = tmp_path / "partly_scanned.pdf"
    document = pymupdf.open()
    first = document.new_page()
    first.insert_text((72, 100), "This page has real text. It should be extracted.", fontsize=11)
    document.new_page()  # no text at all: likely a scanned image page
    document.save(path)
    document.close()

    extracted = PdfExtractor().extract(path)
    assert "This page has real text." in extracted.text
    assert len(extracted.warnings) == 1
    assert "page(s) 2" in extracted.warnings[0]
    assert "ocrmypdf" in extracted.warnings[0]


def test_pdf_extractor_all_scanned_raises_actionable_error(tmp_path: Path) -> None:
    path = tmp_path / "scanned.pdf"
    document = pymupdf.open()
    document.new_page()
    document.save(path)
    document.close()

    with pytest.raises(ExtractionError, match="ocrmypdf"):
        PdfExtractor().extract(path)


def test_pdf_extractor_unreadable_file(tmp_path: Path) -> None:
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"not a pdf")
    with pytest.raises(ExtractionError, match="Could not open PDF"):
        PdfExtractor().extract(path)


def test_format_detection_and_registry(tmp_path: Path) -> None:
    assert format_for_path(Path("book.PDF")) == "pdf"
    assert format_for_path(Path("notes.md")) == "md"
    assert format_for_path(Path("notes.markdown")) == "md"
    assert format_for_path(Path("book.txt")) == "txt"
    assert isinstance(get_extractor("TXT"), TxtExtractor)
    assert isinstance(get_extractor("md"), MarkdownExtractor)
    assert isinstance(get_extractor("pdf", password="x"), PdfExtractor)

    with pytest.raises(UnsupportedFormatError, match="--format"):
        format_for_path(Path("book.docx"))
    with pytest.raises(UnsupportedFormatError, match="Choose one of"):
        get_extractor("docx")


def _write_small_pdf(path: Path, text: str, *, password: str | None = None) -> None:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 100), text, fontsize=11)
    save_kwargs: dict[str, object] = {}
    if password is not None:
        save_kwargs = {
            "encryption": pymupdf.PDF_ENCRYPT_AES_256,
            "owner_pw": "owner",
            "user_pw": password,
        }
    document.save(path, **save_kwargs)
    document.close()
