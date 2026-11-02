from __future__ import annotations

import logging

import pytest

from say_chunker.adapters.pysbd_splitter import PysbdSentenceSplitter
from say_chunker.domain.models import Sentence


@pytest.fixture
def splitter() -> PysbdSentenceSplitter:
    return PysbdSentenceSplitter()


def texts(sentences: list[Sentence]) -> list[str]:
    return [sentence.text for sentence in sentences]


def test_abbreviations_and_initials(splitter: PysbdSentenceSplitter) -> None:
    sentences = splitter.split(
        "Dr. Smith met Mr. Jones at 3 p.m. They discussed the U.S.A. budget. It was late."
    )
    assert texts(sentences) == [
        "Dr. Smith met Mr. Jones at 3 p.m.",
        "They discussed the U.S.A. budget.",
        "It was late.",
    ]


def test_decimals_are_not_split(splitter: PysbdSentenceSplitter) -> None:
    sentences = splitter.split("Pi is 3.14 and e is 2.718. Both are useful.")
    assert texts(sentences) == ["Pi is 3.14 and e is 2.718.", "Both are useful."]


def test_ellipsis(splitter: PysbdSentenceSplitter) -> None:
    sentences = splitter.split("Wait... what did you say? I did not hear you.")
    assert texts(sentences) == [
        "Wait... what did you say?",
        "I did not hear you.",
    ]


def test_quoted_speech(splitter: PysbdSentenceSplitter) -> None:
    sentences = splitter.split('"Hello," she said. "How are you?" He shrugged.')
    assert texts(sentences) == [
        '"Hello," she said.',
        '"How are you?"',
        "He shrugged.",
    ]


def test_paragraph_indexes_are_preserved(splitter: PysbdSentenceSplitter) -> None:
    sentences = splitter.split("First. Second.\n\nThird.\n\n\nFourth.")
    assert [(s.text, s.paragraph_index) for s in sentences] == [
        ("First.", 0),
        ("Second.", 0),
        ("Third.", 1),
        ("Fourth.", 2),
    ]


def test_no_punctuation_is_one_sentence_per_paragraph(
    splitter: PysbdSentenceSplitter,
) -> None:
    sentences = splitter.split("A paragraph without punctuation\n\nAnother one")
    assert texts(sentences) == ["A paragraph without punctuation", "Another one"]


def test_single_sentence_paragraph(splitter: PysbdSentenceSplitter) -> None:
    sentences = splitter.split("Just one sentence.")
    assert texts(sentences) == ["Just one sentence."]


def test_whitespace_only_input_is_empty(splitter: PysbdSentenceSplitter) -> None:
    assert splitter.split(" \n\n \t ") == []


def test_unsupported_language_falls_back_to_regex(
    splitter: PysbdSentenceSplitter, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="say_chunker.adapters.pysbd_splitter"):
        sentences = splitter.split("One. Two. Three.", language="xx")
    assert texts(sentences) == ["One.", "Two.", "Three."]
    assert any("does not support language" in record.message for record in caplog.records)


def test_cjk_fallback_when_pysbd_returns_one_sentence(
    splitter: PysbdSentenceSplitter,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(splitter, "_segment", lambda paragraph, language: [paragraph])
    with caplog.at_level(logging.WARNING, logger="say_chunker.adapters.pysbd_splitter"):
        sentences = splitter.split("你好。世界。这是一个测试。", language="en")
    assert texts(sentences) == ["你好。", "世界。", "这是一个测试。"]
    assert any("CJK" in record.message for record in caplog.records)


def test_cjk_text_that_pysbd_handles_natively(splitter: PysbdSentenceSplitter) -> None:
    sentences = splitter.split("你好。世界。这是一个测试。", language="en")
    assert texts(sentences) == ["你好。", "世界。", "这是一个测试。"]


def test_deterministic(splitter: PysbdSentenceSplitter) -> None:
    text = "Dr. One went home. Dr. Two stayed. They met at 4.5 p.m."
    first = splitter.split(text)
    second = PysbdSentenceSplitter().split(text)
    assert first == second
