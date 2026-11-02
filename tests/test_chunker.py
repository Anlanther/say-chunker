"""Tests for the sentence chunker (highest-risk component)."""

from __future__ import annotations

import logging

import pytest

from say_chunker.domain.models import Sentence
from say_chunker.pipeline.chunker import SentenceChunker, _hard_split, _split_long_sentence


def _sentences(texts: list[str], paragraph: int = 0) -> list[Sentence]:
    return [Sentence(text=t, paragraph_index=paragraph) for t in texts]


def _shapes(chunks: list[object]) -> list[int]:
    return [len(chunk.sentence_indexes) for chunk in chunks]  # type: ignore[attr-defined]


def test_exact_multiples() -> None:
    chunker = SentenceChunker()
    chunks = chunker.chunk(
        _sentences([f"S{i}." for i in range(10)]),
        section_index=0,
        sentences_per_chunk=5,
        max_sentence_chars=2000,
    )
    assert _shapes(chunks) == [5, 5]


def test_non_multiples() -> None:
    chunker = SentenceChunker()
    chunks = chunker.chunk(
        _sentences([f"S{i}." for i in range(7)]),
        section_index=0,
        sentences_per_chunk=5,
        max_sentence_chars=2000,
    )
    assert _shapes(chunks) == [5, 2]


def test_paragraph_boundary_preference() -> None:
    chunker = SentenceChunker()
    sentences = _sentences(["A1.", "A2.", "A3."], paragraph=0) + _sentences(
        ["B1.", "B2.", "B3."], paragraph=1
    )
    chunks = chunker.chunk(
        sentences,
        section_index=0,
        sentences_per_chunk=5,
        max_sentence_chars=2000,
    )
    assert _shapes(chunks) == [3, 3]


def test_section_index_and_offsets() -> None:
    chunker = SentenceChunker()
    first = chunker.chunk(
        _sentences([f"A{i}." for i in range(3)]),
        section_index=0,
        sentences_per_chunk=2,
        max_sentence_chars=2000,
    )
    second = chunker.chunk(
        _sentences([f"B{i}." for i in range(3)]),
        section_index=1,
        sentences_per_chunk=2,
        max_sentence_chars=2000,
        first_index=len(first),
    )
    assert [chunk.section_index for chunk in first] == [0, 0]
    assert [chunk.section_index for chunk in second] == [1, 1]
    assert [chunk.index for chunk in first] == [0, 1]
    assert [chunk.index for chunk in second] == [2, 3]


def test_tail_handling() -> None:
    chunker = SentenceChunker()
    chunks = chunker.chunk(
        _sentences([f"S{i}." for i in range(7)]),
        section_index=0,
        sentences_per_chunk=5,
        max_sentence_chars=2000,
    )
    assert chunks[-1].text.startswith("S5.")
    assert chunks[-1].index == 1


def test_long_sentence_split_warns(caplog: pytest.LogCaptureFixture) -> None:
    chunker = SentenceChunker()
    long = "This is a very long clause, " * 30
    with caplog.at_level(logging.WARNING):
        chunks = chunker.chunk(
            [Sentence(long.strip(), 0)],
            section_index=0,
            sentences_per_chunk=5,
            max_sentence_chars=80,
        )
    assert chunks
    assert any("split" in record.message for record in caplog.records)


def test_split_long_sentence_preserves_text_and_bounds() -> None:
    text = "Alpha, beta; gamma—delta and epsilon " * 20
    pieces = _split_long_sentence(text.strip(), 100)
    assert "".join(pieces) == text.strip()
    assert all(len(piece) <= 100 for piece in pieces)


def test_hard_split_bounds() -> None:
    complete, remainder = _hard_split("word " * 100, 12)
    assert all(len(piece) <= 12 for piece in complete)
    assert len(remainder) <= 12


def test_empty_chunk_filtering() -> None:
    chunker = SentenceChunker()
    chunks = chunker.chunk(
        [Sentence("   ", 0), Sentence("Real.", 0)],
        section_index=0,
        sentences_per_chunk=5,
        max_sentence_chars=2000,
    )
    assert len(chunks) == 1
    assert chunks[0].text == "Real."


def test_all_whitespace_no_chunks() -> None:
    chunker = SentenceChunker()
    chunks = chunker.chunk(
        [Sentence("   ", 0)],
        section_index=0,
        sentences_per_chunk=5,
        max_sentence_chars=2000,
    )
    assert chunks == []


def test_determinism() -> None:
    chunker = SentenceChunker()
    sentences = _sentences([f"S{i} with some words." for i in range(12)])
    first = chunker.chunk(
        sentences, section_index=0, sentences_per_chunk=5, max_sentence_chars=2000
    )
    second = chunker.chunk(
        sentences, section_index=0, sentences_per_chunk=5, max_sentence_chars=2000
    )
    assert [(c.index, c.text, tuple(c.sentence_indexes)) for c in first] == [
        (c.index, c.text, tuple(c.sentence_indexes)) for c in second
    ]
