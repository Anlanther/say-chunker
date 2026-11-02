"""Chunk sentences into groups by count, respecting paragraph + long-sentence rules."""

from __future__ import annotations

import logging
import re

from say_chunker.domain.models import Chunk, Sentence

logger = logging.getLogger(__name__)

_CLAUSE_RE = re.compile(r"([,;]|\s+(?:and|but|or|nor|so|yet)\s+|—|–)", re.IGNORECASE)


class SentenceChunker:
    def chunk(
        self,
        sentences: list[Sentence],
        *,
        section_index: int,
        sentences_per_chunk: int,
        max_sentence_chars: int,
        first_index: int = 0,
    ) -> list[Chunk]:
        """Group ``sentences`` into deterministic chunks for a single section.

        ``first_index`` is the global chunk index offset (the orchestrator passes
        the cumulative count from previous sections so indexes are stable).
        """
        if sentences_per_chunk <= 0:
            raise ValueError("sentences_per_chunk must be a positive integer")

        effective = _expand_long_sentences(sentences, max_sentence_chars)
        effective = [sentence for sentence in effective if sentence.text.strip()]

        chunks: list[Chunk] = []
        i = 0
        total = len(effective)
        chunk_index = first_index
        while i < total:
            start = i
            paragraph = effective[i].paragraph_index
            i += 1
            while i < total and (i - start) < sentences_per_chunk:
                if effective[i].paragraph_index != paragraph:
                    break
                i += 1

            selected = effective[start:i]
            text = " ".join(sentence.text.strip() for sentence in selected)
            if text.strip():
                chunks.append(
                    Chunk(
                        index=chunk_index,
                        section_index=section_index,
                        sentence_indexes=list(range(start, i)),
                        text=text,
                    )
                )
                chunk_index += 1
        return chunks


def _expand_long_sentences(sentences: list[Sentence], max_chars: int) -> list[Sentence]:
    expanded: list[Sentence] = []
    for sentence in sentences:
        if len(sentence.text) <= max_chars:
            expanded.append(sentence)
            continue
        pieces = _split_long_sentence(sentence.text, max_chars)
        logger.warning(
            "sentence of %d chars exceeds max_sentence_chars (%d); split into %d pieces",
            len(sentence.text),
            max_chars,
            len(pieces),
        )
        for piece in pieces:
            expanded.append(Sentence(text=piece, paragraph_index=sentence.paragraph_index))
    return expanded


def _split_long_sentence(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    parts = [part for part in _CLAUSE_RE.split(text) if part != ""]
    pieces: list[str] = []
    buffer = ""
    for part in parts:
        if len(buffer) + len(part) <= max_chars:
            buffer += part
            continue

        if buffer.strip():
            pieces.append(buffer)
            buffer = ""

        if len(part) <= max_chars:
            buffer = part
        else:
            complete, remainder = _hard_split(part, max_chars)
            pieces.extend(complete)
            buffer = remainder

    if buffer.strip():
        pieces.append(buffer)
    return pieces


def _hard_split(text: str, max_chars: int) -> tuple[list[str], str]:
    size = max(1, max_chars)
    complete: list[str] = []
    buffer = ""
    for word in text.split(" "):
        candidate = f"{buffer} {word}".strip() if buffer else word
        if len(candidate) <= size:
            buffer = candidate
            continue

        if buffer:
            complete.append(buffer)
        if len(word) <= size:
            buffer = word
        else:
            complete.extend(_chunk_string(word, size))
            buffer = ""
    return complete, buffer


def _chunk_string(text: str, size: int) -> list[str]:
    return [text[offset : offset + size] for offset in range(0, len(text), size)]
