"""Sentence splitting via ``pysbd`` with CJK / unsupported-language fallback."""

from __future__ import annotations

import logging
import re

from pysbd import Segmenter

from say_chunker.domain.models import Sentence

logger = logging.getLogger(__name__)

_PARAGRAPH_RE = re.compile(r"\n\s*\n")
_CJK_END_RE = re.compile(r"[。！？]")
# Split after ASCII sentence-end punctuation when followed by whitespace/quote/end,
# and after CJK enders unconditionally (CJK sentences are not space-separated).
_FALLBACK_RE = re.compile(r"(?:(?<=[.!?])(?=[\s\"'”’」』]|$)|(?<=[。！？]))")


class PySBDSentenceSplitter:
    def __init__(self) -> None:
        self._segmenters: dict[str, Segmenter | None] = {}

    def split(self, text: str, *, language: str) -> list[Sentence]:
        sentences: list[Sentence] = []
        for paragraph_index, paragraph in enumerate(_split_paragraphs(text)):
            segmenter = self._get_segmenter(language)
            if segmenter is None:
                logger.warning(
                    "language %r is not supported by pysbd; using regex fallback", language
                )
                parts = _fallback_split(paragraph)
            else:
                parts = list(segmenter.segment(paragraph))
                if len(parts) <= 1 and len(_CJK_END_RE.findall(paragraph)) > 1:
                    logger.warning(
                        "pysbd returned a single sentence for CJK text; using regex fallback"
                    )
                    parts = _fallback_split(paragraph)

            for part in parts:
                stripped = part.strip()
                if stripped:
                    sentences.append(Sentence(text=stripped, paragraph_index=paragraph_index))
        return sentences

    def _get_segmenter(self, language: str) -> Segmenter | None:
        if language not in self._segmenters:
            try:
                self._segmenters[language] = Segmenter(language=language, clean=False)
            except (ValueError, KeyError):
                self._segmenters[language] = None
        return self._segmenters[language]


def _split_paragraphs(text: str) -> list[str]:
    if not text.strip():
        return []
    return [part for part in _PARAGRAPH_RE.split(text) if part.strip()]


def _fallback_split(text: str) -> list[str]:
    return [part.strip() for part in _FALLBACK_RE.split(text) if part.strip()]
