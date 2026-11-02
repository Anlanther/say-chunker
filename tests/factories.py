"""Small builders for domain objects used across tests."""

from __future__ import annotations

from pathlib import Path

from say_chunker.domain.models import (
    Chunk,
    JobStatus,
    Sentence,
    SynthesisConfig,
    SynthesisJob,
)


def make_config(**overrides: object) -> SynthesisConfig:
    values: dict[str, object] = {
        "voice": "Samantha",
        "rate": 200,
        "sentences_per_chunk": 5,
        "max_sentence_chars": 2000,
        "language": "en",
        "sleep_between_chunks": 0.5,
        "retries": 2,
        "normalize": True,
        "bitrate_kbps": 128,
        "timeout_seconds": 300.0,
        "silence_between_chunks_ms": 250,
        "max_runtime_seconds": None,
    }
    values.update(overrides)
    return SynthesisConfig(**values)  # type: ignore[arg-type]


def make_chunk(
    index: int = 0,
    *,
    text: str = "Sentence one. Sentence two.",
    sentences: tuple[Sentence, ...] | None = None,
    section_index: int = 0,
    section_title: str | None = None,
    start: int = 0,
    end: int = 1,
) -> Chunk:
    if sentences is None:
        sentences = (
            Sentence("Sentence one.", 0),
            Sentence("Sentence two.", 0),
        )
    return Chunk(
        index=index,
        section_index=section_index,
        section_title=section_title,
        sentences=sentences,
        start_sentence_index=start,
        end_sentence_index=end,
        text=text,
        char_count=len(text),
    )


def make_job(
    *,
    job_id: str = "0123456789abcdef",
    output_path: Path | None = None,
    status: JobStatus = JobStatus.PAUSED,
    completed: frozenset[int] = frozenset(),
    updated_at: str = "2026-01-01T00:00:00+00:00",
    created_at: str = "2026-01-01T00:00:00+00:00",
    config: SynthesisConfig | None = None,
) -> SynthesisJob:
    chunks = (make_chunk(0), make_chunk(1))
    return SynthesisJob(
        job_id=job_id,
        status=status,
        input_path="/tmp/input.txt",
        content_hash="a" * 64,
        extractor="txt",
        output_path=str(output_path or Path("/tmp/out.mp3")),
        config=config or make_config(),
        title="Test",
        page_count=0,
        section_count=1,
        sentence_count=4,
        chunks=chunks,
        completed_chunk_indices=completed,
        warnings=(),
        created_at=created_at,
        updated_at=updated_at,
    )
