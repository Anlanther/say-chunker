"""Core domain models.

These dataclasses are the currency exchanged between the pipeline and its
adapters. They are deliberately free of any third-party or I/O concerns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Sentence:
    """A single sentence with the paragraph it belongs to."""

    text: str
    paragraph_index: int


@dataclass(frozen=True)
class Section:
    """A hard boundary within a document (chapter / heading)."""

    title: str | None
    text: str
    level: int


@dataclass(frozen=True)
class SourceDocument:
    """Output of a :class:`TextExtractor`."""

    title: str
    text: str
    page_count: int
    sections: list[Section]
    warnings: list[str]


@dataclass(frozen=True)
class Chunk:
    """A group of sentences sent to the TTS engine in a single call.

    ``sentence_indexes`` are indexes into the (post long-sentence-split) sentence
    list for the chunk's section. ``index`` is a global, deterministic index
    assigned by the orchestrator across the whole document.
    """

    index: int
    section_index: int
    sentence_indexes: list[int]
    text: str


@dataclass(frozen=True)
class AudioSegment:
    """A single chunk's synthesized audio file."""

    chunk_index: int
    path: Path
    duration_seconds: float | None = None


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class SynthesisJob:
    """Persisted state for a single conversion job."""

    job_id: str
    content_hash: str
    extractor: str
    input_path: str
    title: str
    sections_count: int
    chunks: list[Chunk]
    completed_chunks: set[int]
    status: JobStatus
    config: dict[str, Any]
    output_path: str | None
    created_at: str
    updated_at: str = field(default="")
