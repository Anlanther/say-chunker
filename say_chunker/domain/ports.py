"""Ports (protocols) implemented by adapters.

The pipeline imports only these protocols. Concrete adapters are constructed at
the CLI boundary and injected into the orchestrator.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from say_chunker.domain.models import (
    AudioSegment,
    Chunk,
    DocumentPlan,
    Sentence,
    SourceDocument,
    SynthesisConfig,
    SynthesisJob,
)


class TextExtractor(Protocol):
    @property
    def name(self) -> str: ...

    def extract(self, path: Path) -> SourceDocument: ...


class SentenceSplitter(Protocol):
    def split(self, text: str, *, language: str = "en") -> list[Sentence]: ...


class Chunker(Protocol):
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
    ) -> list[Chunk]: ...


class TTSEngine(Protocol):
    @property
    def name(self) -> str: ...

    def list_voices(self) -> list[str]: ...

    def validate_voice(self, voice: str) -> None: ...

    def synthesize(
        self,
        chunk: Chunk,
        output_path: Path,
        *,
        voice: str,
        rate: int,
        timeout_seconds: float,
    ) -> AudioSegment: ...


class AudioMerger(Protocol):
    def validate(self) -> None: ...

    def merge(
        self,
        segments: list[AudioSegment],
        output_path: Path,
        *,
        normalize: bool,
        bitrate_kbps: int,
        silence_ms: int,
    ) -> None: ...


class JobStore(Protocol):
    def save(self, job: SynthesisJob) -> None: ...

    def load(self, job_id: str) -> SynthesisJob | None: ...

    def list_jobs(self) -> list[SynthesisJob]: ...

    def delete(self, job_id: str) -> None: ...

    def work_dir(self, job_id: str) -> Path: ...

    def log_path(self, job_id: str) -> Path: ...


class Planner(Protocol):
    """Minimal planning interface used by the inspect command."""

    def plan(self, document: SourceDocument, *, config: SynthesisConfig) -> DocumentPlan: ...
