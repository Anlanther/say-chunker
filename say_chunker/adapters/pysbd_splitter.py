"""Sentence splitting backed by pysbd, with a deterministic regex fallback."""

from __future__ import annotations

import logging
import re
import warnings
from importlib import import_module
from typing import Any

from say_chunker.domain.models import Sentence
from say_chunker.utils.textutil import normalize_paragraph, split_paragraphs

with warnings.catch_warnings():
    # pysbd 0.3.4 contains invalid regex escape sequences that emit
    # SyntaxWarning on first import under newer Pythons.
    warnings.simplefilter("ignore", SyntaxWarning)
    pysbd: Any = import_module("pysbd")

logger = logging.getLogger(__name__)

_CJK_PUNCTUATION = "。！？"
_CJK_PUNCT_RE = re.compile(r"[。！？]")
# Split after runs of terminators plus optional closing quotes. ASCII
# terminators only split when followed by whitespace/end, so decimals such as
# 3.14 survive even in fallback mode.
_TERMINATOR_RE = re.compile(r"[。！？]+[”’\"']*|[.!?]+[”’\"']*(?=\s|$)")


class PysbdSentenceSplitter:
    """Split paragraphs with pysbd and assign local paragraph indexes.

    pysbd supports abbreviations, initials, decimals, ellipses, and quoted
    speech. If the requested language is unsupported, or pysbd leaves
    CJK-punctuated text as one giant sentence, a regex splitter is used and a
    warning is logged.
    """

    name = "pysbd"

    def __init__(self) -> None:
        self._segmenters: dict[str, Any] = {}

    def split(self, text: str, *, language: str = "en") -> list[Sentence]:
        paragraphs = split_paragraphs(text)
        sentences: list[Sentence] = []
        regex_mode = False
        cjk_warned = False

        for paragraph_index, paragraph in enumerate(paragraphs):
            if regex_mode:
                parts = _regex_split(paragraph)
            else:
                try:
                    parts = self._segment(paragraph, language)
                except (ValueError, LookupError) as exc:
                    logger.warning(
                        "pysbd does not support language %r (%s); "
                        "falling back to regex sentence splitting.",
                        language,
                        exc,
                    )
                    regex_mode = True
                    parts = _regex_split(paragraph)
                except Exception as exc:  # pysbd is third-party and not defensive
                    logger.warning(
                        "pysbd failed on a paragraph (%s); "
                        "falling back to regex sentence splitting.",
                        exc,
                    )
                    regex_mode = True
                    parts = _regex_split(paragraph)

            if not regex_mode and _needs_cjk_fallback(paragraph, parts):
                if not cjk_warned:
                    logger.warning(
                        "pysbd returned a single sentence for CJK-punctuated text; "
                        "using the CJK punctuation fallback splitter."
                    )
                    cjk_warned = True
                parts = _regex_split(paragraph)
            elif not regex_mode and not parts:
                parts = _regex_split(paragraph)

            for part in parts:
                cleaned = normalize_paragraph(part)
                if cleaned:
                    sentences.append(Sentence(text=cleaned, paragraph_index=paragraph_index))

        return sentences

    def _segment(self, paragraph: str, language: str) -> list[str]:
        segmenter = self._segmenters.get(language)
        if segmenter is None:
            segmenter = pysbd.Segmenter(language=language, clean=False)
            self._segmenters[language] = segmenter
        result = segmenter.segment(paragraph)
        return [str(part) for part in result]


def _needs_cjk_fallback(paragraph: str, parts: list[str]) -> bool:
    if len(parts) > 1:
        return False
    terminators = _CJK_PUNCT_RE.findall(paragraph)
    if not terminators:
        return False
    if len(terminators) >= 2:
        return True
    return not paragraph.rstrip().endswith(tuple(_CJK_PUNCTUATION))


def _regex_split(paragraph: str) -> list[str]:
    parts: list[str] = []
    start = 0
    for match in _TERMINATOR_RE.finditer(paragraph):
        piece = paragraph[start : match.end()].strip()
        if piece:
            parts.append(piece)
        start = match.end()
    tail = paragraph[start:].strip()
    if tail:
        parts.append(tail)
    return parts or [paragraph]
