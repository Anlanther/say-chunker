"""Ports (Protocols) defining the seams the orchestrator depends on.

The orchestrator only ever sees these protocols; concrete adapters are
constructed at the CLI boundary and injected in.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from say_chunker.domain.models import Chunk, Sentence, SourceDocument, SynthesisJob


class TTSEngine(Protocol):
    def synthesize(
        self,
        text: str,
        output_path: Path,
        *,
        voice: str,
        rate: int,
        timeout_seconds: float,
    ) -> Path: ...


class TextExtractor(Protocol):
    name: str

    def extract(self, path: Path, *, password: str | None = None) -> SourceDocument: ...


class SentenceSplitter(Protocol):
    def split(self, text: str, *, language: str) -> list[Sentence]: ...


class Chunker(Protocol):
    def chunk(
        self,
        sentences: list[Sentence],
        *,
        section_index: int,
        sentences_per_chunk: int,
        max_sentence_chars: int,
        first_index: int = 0,
    ) -> list[Chunk]: ...


class AudioMerger(Protocol):
    def merge(
        self,
        segments: list[Path],
        output_path: Path,
        *,
        bitrate_kbps: int,
        normalize: bool,
        silence_between_ms: int,
    ) -> Path: ...


class JobStore(Protocol):
    def save(self, job: SynthesisJob) -> None: ...
    def load(self, job_id: str) -> SynthesisJob | None: ...
    def list_jobs(self) -> list[SynthesisJob]: ...
    def delete(self, job_id: str) -> None: ...
    def clean(self, *, older_than_days: float | None) -> int: ...
