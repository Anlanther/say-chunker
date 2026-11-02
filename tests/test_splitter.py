"""Tests for the pysbd sentence splitter and its fallbacks."""

from __future__ import annotations

import logging

import pytest

from say_chunker.adapters.pysbd_splitter import PySBDSentenceSplitter, _fallback_split
from say_chunker.domain.models import Sentence


def _texts(sentences: list[Sentence]) -> list[str]:
    return [s.text for s in sentences]


def test_abbreviations() -> None:
    splitter = PySBDSentenceSplitter()
    assert len(splitter.split("Dr. Smith went to Washington D.C. today.", language="en")) == 1


def test_decimals() -> None:
    splitter = PySBDSentenceSplitter()
    assert len(splitter.split("The price is 3.14 and it works.", language="en")) == 1


def test_ellipses() -> None:
    splitter = PySBDSentenceSplitter()
    assert len(splitter.split("Wait... what?", language="en")) == 1


def test_quoted_speech() -> None:
    splitter = PySBDSentenceSplitter()
    out = splitter.split('He said "hello world." Then left.', language="en")
    assert _texts(out) == ['He said "hello world."', "Then left."]


def test_no_punctuation() -> None:
    splitter = PySBDSentenceSplitter()
    assert len(splitter.split("A long sentence with no punctuation at all", language="en")) == 1


def test_single_sentence_paragraph() -> None:
    splitter = PySBDSentenceSplitter()
    out = splitter.split("One.\n\nTwo.", language="en")
    assert [s.paragraph_index for s in out] == [0, 1]
    assert _texts(out) == ["One.", "Two."]


def test_cjk_unsupported_language_fallback() -> None:
    splitter = PySBDSentenceSplitter()
    out = splitter.split("你好。世界！今天？", language="xx")
    assert _texts(out) == ["你好。", "世界！", "今天？"]


def test_cjk_single_sentence_fallback(caplog: pytest.LogCaptureFixture) -> None:
    class Stub:
        def segment(self, text: str) -> list[str]:
            return [text]

    splitter = PySBDSentenceSplitter()
    splitter._segmenters["en"] = Stub()  # type: ignore[assignment]
    with caplog.at_level(logging.WARNING):
        out = splitter.split("你好。世界！今天？", language="en")
    assert _texts(out) == ["你好。", "世界！", "今天？"]
    assert any("fallback" in record.message for record in caplog.records)


def test_fallback_split_direct() -> None:
    assert _fallback_split("你好。世界！今天？") == ["你好。", "世界！", "今天？"]
    assert _fallback_split("Hello. World.") == ["Hello.", "World."]
    assert _fallback_split("The price is 3.14. Next.") == ["The price is 3.14.", "Next."]
