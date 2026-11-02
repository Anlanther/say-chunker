"""Generate the committed test fixtures.

Run with: uv run python tests/fixtures/build_fixtures.py
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pymupdf

HERE = Path(__file__).parent


def build_text() -> None:
    sentences = [
        "The morning light moved slowly across the quiet kitchen floor.",
        "A kettle began to whistle, and the dog lifted its head.",
        "Outside, the street was still wet from the rain that had fallen overnight.",
        "Nobody had expected the weather to change so quickly.",
        "By seven o'clock, the buses were running again.",
        "The baker unlocked the shop door and turned the sign to open.",
        "A small crowd gathered near the station, waiting for news.",
        "The clock above the platform was three minutes slow.",
        "A child asked why the trains were late, and her father shrugged.",
        "Somewhere down the line, a signal had failed during the storm.",
        "The stationmaster apologized over the loudspeaker.",
        "His voice echoed twice before the message ended.",
        "People checked their phones and then looked at the sky.",
        "The clouds were thin now, and the wind had dropped.",
        "A woman bought a coffee from the cart beside the ticket office.",
        "The steam curled upward and disappeared into the cold air.",
        "Two students compared notes for an exam they had studied for all week.",
        "One of them remembered a formula; the other remembered a story.",
        "They laughed because neither memory seemed useful now.",
        "The platform clock clicked forward one reluctant minute.",
        "At last, a train appeared in the distance, moving slowly.",
        "The crowd leaned forward as if a single step would hurry it along.",
        "The doors opened with a sigh, and everyone stepped inside.",
        "The carriage smelled of rain, coffee, and old upholstery.",
        "A man in a blue coat found a seat near the window.",
        "He watched the town slide away and become fields.",
        "The fields were brown and gold, divided by low stone walls.",
        "A flock of birds rose from a tree and crossed the tracks.",
        "The train passed a station without stopping.",
        "Its name was painted on a board, but the rain had faded the letters.",
        "For a moment, the carriage was silent except for the wheels.",
        "Then someone opened a newspaper, and the pages rustled.",
        "A baby laughed at the sound and was gently shushed.",
        "The conductor walked through, punching tickets and nodding.",
        "He told a passenger that the delay would not be repeated.",
        "The passenger nodded, though neither of them believed it.",
        "By the time the train reached the city, the sun was high.",
        "The streets were crowded, and the air smelled of bread and diesel.",
        "The man in the blue coat stepped onto the platform and stretched.",
        "He had a meeting at nine, a letter to post, and a promise to keep.",
    ]

    paragraphs: list[str] = []
    index = 0
    while sum(len(paragraph) for paragraph in paragraphs) < 12_000:
        chunk = sentences[index % len(sentences) : index % len(sentences) + 4]
        if len(chunk) < 4:
            chunk = sentences[: 4 - len(chunk)] + chunk
        paragraph = " ".join(chunk)
        paragraphs.append(f"Paragraph {len(paragraphs) + 1}. {paragraph}")
        index += 4

    (HERE / "long_sample.txt").write_text("\n\n".join(paragraphs) + "\n", encoding="utf-8")


def build_markdown() -> None:
    markdown = """---
title: This front matter should not be spoken
---

# The Markdown Sample

This introduction has **bold text**, *italic text*, [a link](https://example.com),
`inline code`, and an image: ![a small diagram](diagram.png).

It also has a second paragraph with "quoted speech" and a decimal such as 3.14.

## First Section

The first section contains a short list:

- First item with **emphasis**.
- Second item with a [link](https://example.com/two).
- Third item with `code`.

> A block quote should still be read aloud.

## Second Section

The second section has a fenced code block:

```python
print("hello from a code block")
```

And a final paragraph that ends the sample. It has two sentences.
"""
    (HERE / "sample.md").write_text(markdown, encoding="utf-8")


def insert_paragraph(page: pymupdf.Page, y: float, text: str) -> float:
    wrapped = textwrap.wrap(text.replace("\n", " "), width=88) or [text]
    embedded_newlines = text.count("\n")
    height = (len(wrapped) + embedded_newlines + 2) * 15 + 6
    rect = pymupdf.Rect(72, y, 540, y + height)
    page.insert_textbox(rect, text, fontsize=11, lineheight=1.25)
    return y + height + 12


def build_pdf() -> None:
    document = pymupdf.open()
    chapter_one = [
        "Chapter One",
        (
            "The village of Ashford stood at the edge of a wide green valley. "
            "Its houses were old, its roads were narrow, and its river ran clear "
            "after every storm. Travellers sometimes stopped there by accident, "
            "and a few of them decided to stay."
        ),
        (
            "On the first morning of spring, a letter arrived at the post office. "
            "It had no stamp, no return address, and no name on the envelope. "
            "The postmistress turned it over twice before she called the mayor."
        ),
        (
            "The mayor opened the letter in the square, while half the village watched. "
            "Inside was a single sheet of paper with a map drawn in blue ink. "
            "The map showed a path that nobody recognized."
        ),
        (
            "The blacksmith said the map was a joke, but he copied it anyway. "
            "The schoolteacher said it was a lesson, though she could not explain why. "
            "By sunset, three copies were pinned to the door of the inn."
        ),
        (
            "Later, the postmistress remembered the handwriting. It looked like her own. "
            "She searched her desk for a pen and found the drawer empty. "
            "For a long moment, she said nothing to anyone."
        ),
    ]
    chapter_two = [
        "Chapter Two",
        (
            "By noon, the path had become the only subject of conversation. "
            "Some said it led to the old mill; others said it led nowhere at all. "
            "The children were forbidden to follow it, which naturally made them curious."
        ),
        (
            "The exam-"
            "\nple was difficult, but the teacher had prepared them well. "
            "Every student remembered the lesson about maps and misleading signs. "
            "They promised to stay on the road and to be home before dark."
        ),
        (
            "That evening, the mayor walked to the edge of the village alone. "
            "He carried a lantern, the letter, and a compass that had belonged to his father. "
            "The path began between two apple trees, exactly where the map said it would."
        ),
        (
            "He followed it until the lantern light reached a low stone wall. "
            "Beyond the wall, the valley opened into a field of pale grass. "
            "The compass needle turned once and then became still."
        ),
    ]

    pages = [
        chapter_one[:2],
        chapter_one[2:4],
        chapter_one[4:],
        chapter_two[:2],
        chapter_two[2:4],
        chapter_two[4:],
    ]

    for page_number, paragraphs in enumerate(pages, start=1):
        page = document.new_page(width=612, height=792)
        page.insert_text((72, 40), "The Great Test Book", fontsize=9)
        page.insert_text((72, 760), f"Page {page_number}", fontsize=9)
        page.insert_text((300, 740), str(page_number), fontsize=9)

        y = 110.0
        for paragraph in paragraphs:
            y = insert_paragraph(page, y, paragraph)

    document.set_metadata({"title": "The Great Test Book", "author": "Fixture Author"})
    document.set_toc(
        [
            [1, "Chapter One", 1],
            [1, "Chapter Two", 4],
        ]
    )
    document.save(HERE / "sample.pdf", garbage=4, deflate=True)
    document.close()


if __name__ == "__main__":
    build_text()
    build_markdown()
    build_pdf()
    print("Fixtures written to", HERE)
