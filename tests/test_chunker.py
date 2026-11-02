from __future__ import annotations

import pytest

from say_chunker.domain.models import Chunk, Sentence
from say_chunker.pipeline.chunker import SentenceCountChunker


def sentences(*texts: str, paragraph: int = 0) -> list[Sentence]:
    return [Sentence(text=text, paragraph_index=paragraph) for text in texts]


def numbers(*values: int, paragraph: int = 0) -> list[Sentence]:
    return sentences(*(f"Sentence {value}." for value in values), paragraph=paragraph)


def chunk(
    items: list[Sentence],
    *,
    n: int = 5,
    max_chars: int = 2000,
    section_index: int = 0,
    section_title: str | None = None,
    sentence_offset: int = 0,
    chunk_offset: int = 0,
) -> list[Chunk]:
    return SentenceCountChunker().chunk(
        items,
        sentences_per_chunk=n,
        max_sentence_chars=max_chars,
        section_index=section_index,
        section_title=section_title,
        sentence_offset=sentence_offset,
        chunk_offset=chunk_offset,
    )


def test_exact_multiple() -> None:
    chunks = chunk(numbers(*range(10)), n=5)
    assert [len(c.sentences) for c in chunks] == [5, 5]
    assert [(c.index, c.start_sentence_index, c.end_sentence_index) for c in chunks] == [
        (0, 0, 4),
        (1, 5, 9),
    ]


def test_non_multiple_has_shorter_tail() -> None:
    chunks = chunk(numbers(*range(7)), n=5)
    assert [len(c.sentences) for c in chunks] == [5, 2]
    assert [c.text for c in chunks] == [
        "Sentence 0. Sentence 1. Sentence 2. Sentence 3. Sentence 4.",
        "Sentence 5. Sentence 6.",
    ]


def test_paragraph_boundaries_are_preferred() -> None:
    items = sentences("A1.", "A2.", "A3.", paragraph=0) + sentences(
        "B1.", "B2.", "B3.", paragraph=1
    )
    chunks = chunk(items, n=5)
    assert [len(c.sentences) for c in chunks] == [3, 3]
    assert all(len({s.paragraph_index for s in c.sentences}) == 1 for c in chunks)


def test_paragraph_break_closes_chunk_even_with_room_left() -> None:
    items = sentences("A1.", "A2.", paragraph=0) + sentences("B1.", "B2.", "B3.", paragraph=1)
    chunks = chunk(items, n=5)
    assert [len(c.sentences) for c in chunks] == [2, 3]


def test_section_boundary_is_not_crossed() -> None:
    section_a = chunk(
        sentences("A1.", "A2."),
        section_index=0,
        section_title="Chapter A",
        sentence_offset=0,
        chunk_offset=0,
    )
    section_b = chunk(
        sentences("B1.", "B2.", "B3."),
        section_index=1,
        section_title="Chapter B",
        sentence_offset=2,
        chunk_offset=len(section_a),
    )
    assert [c.section_index for c in section_a] == [0]
    assert [c.section_index for c in section_b] == [1]
    assert [c.section_title for c in section_a] == ["Chapter A"]
    assert [c.section_title for c in section_b] == ["Chapter B"]
    assert section_b[0].start_sentence_index == 2
    assert [c.index for c in section_a + section_b] == [0, 1]
    assert section_b[0].index == 1


def test_tail_short_chunk_at_end_of_section() -> None:
    chunks = chunk(numbers(*range(12)), n=5)
    assert [len(c.sentences) for c in chunks] == [5, 5, 2]
    assert chunks[-1].start_sentence_index == 10
    assert chunks[-1].end_sentence_index == 11


def test_long_sentence_is_split_at_clauses_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    long_text = (
        "This is a very long sentence, with several commas; it also has a semicolon "
        "and an em dash — plus a conjunction and another clause for good measure."
    )
    chunks = chunk(sentences(long_text), n=5, max_chars=60)
    assert len(chunks) == 1
    assert len(chunks[0].sentences) >= 2
    assert all(len(part.text) <= 60 for part in chunks[0].sentences)
    assert chunks[0].start_sentence_index == 0
    assert chunks[0].end_sentence_index == 0
    assert "split into" in caplog.text


def test_long_sentence_without_clauses_is_hard_split_without_loss(
    caplog: pytest.LogCaptureFixture,
) -> None:
    original = "word " * 40
    chunks = chunk(sentences(original.strip()), n=1, max_chars=50)
    assert len(chunks) >= 3
    assert all(len(c.sentences) == 1 for c in chunks)
    joined = " ".join(c.text for c in chunks)
    assert joined.replace(" ", "") == original.replace(" ", "")
    assert "split into" in caplog.text


def test_long_sentence_parts_count_toward_sentence_limit() -> None:
    long_text = "first, second, third, fourth, fifth, sixth, seventh, eighth, ninth, tenth"
    chunks = chunk(sentences(long_text), n=4, max_chars=20)
    assert all(len(c.sentences) <= 4 for c in chunks)
    assert sum(len(c.sentences) for c in chunks) == 5
    assert len(chunks) >= 2


def test_exactly_max_length_is_not_split() -> None:
    text = "a" * 50
    chunks = chunk(sentences(text), n=5, max_chars=50)
    assert len(chunks) == 1
    assert len(chunks[0].sentences) == 1


def test_empty_and_whitespace_sentences_are_filtered() -> None:
    items = [
        Sentence("", 0),
        Sentence("   ", 0),
        Sentence("Real sentence.", 0),
        Sentence("\n\t", 0),
    ]
    chunks = chunk(items, n=5)
    assert len(chunks) == 1
    assert chunks[0].text == "Real sentence."
    assert len(chunks[0].sentences) == 1


def test_all_empty_sentences_produce_no_chunks() -> None:
    assert chunk([Sentence("", 0), Sentence("  ", 1)], n=5) == []


def test_deterministic() -> None:
    items = numbers(*range(17))
    first = chunk(items, n=5)
    second = SentenceCountChunker().chunk(
        items,
        sentences_per_chunk=5,
        max_sentence_chars=2000,
    )
    assert first == second


def test_char_count_and_text() -> None:
    chunks = chunk(sentences("One.", "Two."), n=5)
    assert chunks[0].text == "One. Two."
    assert chunks[0].char_count == len("One. Two.")


def test_invalid_sentence_count_raises() -> None:
    with pytest.raises(Exception, match="at least 1"):
        chunk(numbers(1), n=0)


def test_one_sentence_chunks() -> None:
    chunks = chunk(numbers(*range(3)), n=1)
    assert [len(c.sentences) for c in chunks] == [1, 1, 1]
    assert [c.index for c in chunks] == [0, 1, 2]
