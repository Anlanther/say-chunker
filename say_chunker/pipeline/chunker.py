"""Deterministic sentence-counted chunking.

The chunker is called once per section by the planner, so section boundaries are
hard boundaries by construction. Within a section it closes chunks early at
paragraph breaks and keeps chunk boundaries stable across runs.
"""

from __future__ import annotations

import logging
import re

from say_chunker.domain.errors import ConfigurationError
from say_chunker.domain.models import Chunk, Sentence

logger = logging.getLogger(__name__)

_CLAUSE_RE = re.compile(r"[,;:，；：]\s*|[—–]\s*|\s+-\s+")
_CONJUNCTION_RE = re.compile(
    r"\s(?=(?:and|but|or|nor|for|so|yet|because|although|though|while|whereas|"
    r"however|therefore|which|that|when|if|after|before|since|unless|until)\b)",
    re.IGNORECASE,
)


class SentenceCountChunker:
    """Group consecutive sentences into chunks of at most N speakable units."""

    def chunk(
        self,
        sentences: list[Sentence],
        *,
        sentences_per_chunk: int,
        max_sentence_chars: int,
        section_index: int = 0,
        section_title: str | None = None,
        sentence_offset: int = 0,
        chunk_offset: int = 0,
    ) -> list[Chunk]:
        if sentences_per_chunk < 1:
            raise ConfigurationError("--sentences-per-chunk must be at least 1")
        if max_sentence_chars < 1:
            raise ConfigurationError("--max-sentence-chars must be at least 1")

        expanded: list[tuple[int, Sentence]] = []
        for original_index, sentence in enumerate(sentences):
            cleaned = sentence.text.strip()
            if not cleaned:
                continue
            if len(cleaned) <= max_sentence_chars:
                expanded.append(
                    (
                        original_index,
                        Sentence(text=cleaned, paragraph_index=sentence.paragraph_index),
                    )
                )
                continue
            parts = _split_long_sentence(
                cleaned,
                max_sentence_chars,
                paragraph_index=sentence.paragraph_index,
            )
            for part in parts:
                expanded.append(
                    (original_index, Sentence(text=part, paragraph_index=sentence.paragraph_index))
                )

        chunks: list[Chunk] = []
        current: list[Sentence] = []
        current_start = 0
        current_end = 0
        current_paragraph: int | None = None

        def flush() -> None:
            nonlocal current, current_start, current_end, current_paragraph
            if not current:
                return
            text = " ".join(sentence.text for sentence in current)
            chunks.append(
                Chunk(
                    index=chunk_offset + len(chunks),
                    section_index=section_index,
                    section_title=section_title,
                    sentences=tuple(current),
                    start_sentence_index=sentence_offset + current_start,
                    end_sentence_index=sentence_offset + current_end,
                    text=text,
                    char_count=len(text),
                )
            )
            current = []
            current_paragraph = None

        for original_index, sentence in expanded:
            if current and (
                sentence.paragraph_index != current_paragraph or len(current) >= sentences_per_chunk
            ):
                flush()

            if not current:
                current_start = original_index
                current_paragraph = sentence.paragraph_index

            current.append(sentence)
            current_end = original_index

            if len(current) >= sentences_per_chunk:
                flush()

        flush()
        return chunks


def _split_long_sentence(
    text: str,
    max_chars: int,
    *,
    paragraph_index: int,
) -> list[str]:
    """Split an over-long sentence at clause/word boundaries, never truncating."""
    parts: list[str] = []
    remaining = text.strip()

    while len(remaining) > max_chars:
        window = remaining[:max_chars]
        cut = _best_cut(window, max_chars)
        if cut <= 0:
            cut = min(max_chars, len(remaining))
        piece = remaining[:cut].strip()
        if piece:
            parts.append(piece)
        remaining = remaining[cut:].strip()

    if remaining:
        parts.append(remaining)

    logger.warning(
        "Sentence in paragraph %d exceeded %d characters (%d chars); "
        "split into %d part(s) at clause boundaries.",
        paragraph_index,
        max_chars,
        len(text),
        len(parts),
    )
    return parts or [text]


def _best_cut(window: str, max_chars: int) -> int:
    minimum = max(1, int(max_chars * 0.35))
    candidates: list[int] = []

    for match in _CLAUSE_RE.finditer(window):
        if minimum <= match.end() <= len(window):
            candidates.append(match.end())
    for match in _CONJUNCTION_RE.finditer(window):
        if minimum <= match.start() <= len(window):
            candidates.append(match.start())

    if candidates:
        return max(candidates)

    space = window.rfind(" ")
    if space >= minimum:
        return space
    return len(window)
