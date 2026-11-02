"""Core domain models.

These types are deliberately framework-free: no pydantic, no typer, no
filesystem libraries. Adapters translate external data into these shapes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

# ---------------------------------------------------------------------------
# Source documents
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Section:
    """A hard section boundary (chapter, heading, TOC entry).

    ``title`` is metadata for progress/state; ``text`` is the speakable content
    belonging to the section. ``level`` is the heading/TOC depth (0 when the
    source has no structural information).
    """

    title: str | None
    text: str
    level: int = 0


@dataclass(frozen=True, slots=True)
class SourceDocument:
    """Extracted text plus enough structure to plan synthesis."""

    title: str
    text: str
    page_count: int
    sections: list[Section]
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Sentence splitting and chunking
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Sentence:
    """A sentence and the paragraph it belongs to.

    ``paragraph_index`` is local to the section that was split. Chunking must
    never merge sentences from different paragraphs or sections when avoidable.
    """

    text: str
    paragraph_index: int


@dataclass(frozen=True, slots=True)
class Chunk:
    """One unit of work for the TTS engine.

    ``start_sentence_index`` and ``end_sentence_index`` are inclusive indexes
    into the section's original sentence sequence. A long sentence that was
    split by the safety net may contribute multiple ``Sentence`` objects to a
    chunk while keeping the same original sentence index.
    """

    index: int
    section_index: int
    section_title: str | None
    sentences: tuple[Sentence, ...]
    start_sentence_index: int
    end_sentence_index: int
    text: str
    char_count: int

    @property
    def sentence_count(self) -> int:
        """Number of speakable sentence units (after long-sentence splitting)."""
        return len(self.sentences)


@dataclass(frozen=True, slots=True)
class DocumentPlan:
    """Deterministic result of splitting and chunking a source document."""

    chunks: tuple[Chunk, ...]
    sentence_count: int
    speakable_sentence_count: int
    section_count: int
    warnings: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AudioSegment:
    """A synthesized audio file for one chunk."""

    chunk_index: int
    path: Path
    duration_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class SynthesisConfig:
    """Everything that controls synthesis, merging, and runtime safety.

    The job id deliberately covers only ``voice``, ``rate``,
    ``sentences_per_chunk``, ``language``, and the input hash. The remaining
    fields are runtime or merge controls; changing them does not invalidate
    already-synthesized chunk audio.
    """

    voice: str = "Samantha"
    rate: int = 200
    sentences_per_chunk: int = 5
    max_sentence_chars: int = 2000
    language: str = "en"
    sleep_between_chunks: float = 0.5
    retries: int = 2
    normalize: bool = True
    bitrate_kbps: int = 128
    timeout_seconds: float = 300.0
    silence_between_chunks_ms: int = 250
    max_runtime_seconds: float | None = None

    def plan_signature(self) -> tuple[str, int, int, str, int]:
        """Fields that determine chunk boundaries and chunk text."""
        return (
            self.voice,
            self.rate,
            self.sentences_per_chunk,
            self.language,
            self.max_sentence_chars,
        )


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SynthesisJob:
    """Persisted state for one resumable conversion."""

    job_id: str
    status: JobStatus
    input_path: str | None
    content_hash: str
    extractor: str
    output_path: str
    config: SynthesisConfig
    title: str
    page_count: int
    section_count: int
    sentence_count: int
    chunks: tuple[Chunk, ...]
    completed_chunk_indices: frozenset[int]
    warnings: tuple[str, ...]
    created_at: str
    updated_at: str
    schema_version: int = 1
    last_error: str | None = None

    @property
    def chunk_count(self) -> int:
        return len(self.chunks)

    @property
    def completed_count(self) -> int:
        return len(self.completed_chunk_indices)


# ---------------------------------------------------------------------------
# Progress / results
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    """Emitted after a chunk finishes (or is found already complete)."""

    job_id: str
    chunk_index: int
    total_chunks: int
    sentence_start: int
    sentence_end: int
    char_count: int
    elapsed_seconds: float
    eta_seconds: float | None
    chunks_remaining: int


@dataclass(frozen=True, slots=True)
class ConversionResult:
    job_id: str
    status: JobStatus
    output_path: Path
    total_chunks: int
    completed_chunks: int
    synthesized_chunks: int
    elapsed_seconds: float
    message: str | None = None
