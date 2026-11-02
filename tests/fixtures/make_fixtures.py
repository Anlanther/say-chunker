"""Generate committed test fixtures (idempotent). Run: uv run python tests/fixtures/make_fixtures.py"""

from __future__ import annotations

from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent


def make_txt() -> None:
    sentences = [
        "The morning light filtered through the tall windows, casting long shadows across the quiet room.",
        "She had waited for this moment for years, and yet now that it had arrived she felt strangely calm.",
        "Outside, the city was beginning to wake, and the first buses rumbled down the wet streets.",
        "He poured two cups of coffee and set one down beside the stack of unread letters.",
        "Neither of them spoke for a while; the silence was comfortable, familiar, and unhurried.",
    ]
    paragraphs: list[str] = []
    for i in range(90):
        paragraphs.append(" ".join(sentences))
    (HERE / "sample.txt").write_text("\n\n".join(paragraphs), encoding="utf-8")


def make_md() -> None:
    text = """# The Quiet Coast

## Chapter One: Arrival

The train pulled into the station just after noon. A salt breeze moved through the open
windows, carrying the smell of the harbor and the faint cry of gulls.

She stepped onto the platform with a single suitcase and no particular plan.

- The ticket office was closed.
- The cafe across the street was open.
- The innkeeper waved from the doorway.

## Chapter Two: The Harbor

The harbor was smaller than she remembered. Fishing boats knocked gently against the
wooden piers, and a handful of tourists watched from the rail.

### The Lighthouse

The lighthouse stood on the far point, white against the gray sky. It had not been lit
in years, but its keeper still climbed the stairs every evening out of habit.

## Chapter Three: Departure

By the end of the week, the suitcase was heavier than it had been. The town had given
her more than she expected, and leaving felt like leaving a part of herself behind.
"""
    (HERE / "sample.md").write_text(text, encoding="utf-8")


def make_pdf() -> None:
    doc = pymupdf.open()
    for i in range(4):
        page = doc.new_page(width=595, height=842)
        page.insert_text((50, 40), "My Book", fontsize=10)
        page.insert_text((550, 820), str(i + 1), fontsize=10)
        body = f"Chapter {i + 1}. " + " ".join(
            f"Sentence {j} describes the events of chapter {i + 1} in detail." for j in range(60)
        )
        page.insert_textbox(pymupdf.Rect(50, 90, 545, 790), body, fontsize=11)

    doc.set_toc([[1, "Chapter 1", 1], [1, "Chapter 2", 2], [1, "Chapter 3", 3]])
    doc.set_metadata({"title": "My Book"})
    doc.save(str(HERE / "sample.pdf"))
    doc.close()


if __name__ == "__main__":
    make_txt()
    make_md()
    make_pdf()
    print("fixtures written")
