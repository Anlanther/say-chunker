"""Pipeline orchestrator: extract -> split -> chunk -> synth -> merge.

Depends only on ports, never on concrete adapters.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from say_chunker.config import Settings, parse_duration
from say_chunker.domain.errors import (
    ConfigMismatchError,
    JobNotFoundError,
    JobStateMismatchError,
    MergeError,
    OutputNotWritableError,
    SynthesisError,
)
from say_chunker.domain.models import Chunk, JobStatus, SourceDocument, SynthesisJob
from say_chunker.domain.ports import (
    AudioMerger,
    Chunker,
    JobStore,
    SentenceSplitter,
    TextExtractor,
    TTSEngine,
)
from say_chunker.pipeline.throttle import Throttle


def content_hash_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_job_id(
    content_hash: str,
    voice: str,
    rate: int,
    sentences_per_chunk: int,
    language: str,
) -> str:
    raw = f"{content_hash}|{voice}|{rate}|{sentences_per_chunk}|{language}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def chunk_audio_path(temp_dir: Path, job_id: str, chunk_index: int) -> Path:
    return temp_dir / job_id / f"chunk_{chunk_index:05d}.aiff"


@dataclass(frozen=True)
class ProgressEvent:
    chunk_index: int
    total_chunks: int
    sentence_start: int
    sentence_end: int
    chars: int
    elapsed_seconds: float
    eta_seconds: float | None
    status: str


class Orchestrator:
    def __init__(
        self,
        *,
        splitter: SentenceSplitter,
        chunker: Chunker,
        engine: TTSEngine,
        merger: AudioMerger,
        store: JobStore,
        throttle: Throttle,
        temp_dir: Path,
        on_progress: Callable[[ProgressEvent], None] | None = None,
    ) -> None:
        self._splitter = splitter
        self._chunker = chunker
        self._engine = engine
        self._merger = merger
        self._store = store
        self._throttle = throttle
        self._temp_dir = temp_dir
        self._on_progress = on_progress
        self._stop_requested = False
        self._logger = logging.getLogger(__name__)

    def request_stop(self) -> None:
        self._stop_requested = True

    def run(
        self,
        *,
        extractor: TextExtractor,
        input_path: Path,
        output_path: Path | None,
        config: Settings,
        content_hash: str,
        resume: bool,
    ) -> SynthesisJob:
        job_id = make_job_id(
            content_hash, config.voice, config.rate, config.sentences_per_chunk, config.language
        )
        if resume:
            existing = self._store.load(job_id)
            if existing is not None:
                return self._resume(existing, extractor, config, output_path)
        return self._fresh(job_id, content_hash, extractor, input_path, output_path, config)

    def resume(self, *, job_id: str, extractor: TextExtractor, config: Settings) -> SynthesisJob:
        job = self._store.load(job_id)
        if job is None:
            raise JobNotFoundError(f"no job found with id {job_id!r}")
        return self._resume(job, extractor, config, None)

    # -- internal -----------------------------------------------------------

    def _fresh(
        self,
        job_id: str,
        content_hash: str,
        extractor: TextExtractor,
        input_path: Path,
        output_path: Path | None,
        config: Settings,
    ) -> SynthesisJob:
        doc, chunks = self._extract_and_chunk(extractor, input_path, config)
        self._log_warnings(doc)
        resolved_output = self._resolve_output(output_path, doc.title)
        self._ensure_output_writable(resolved_output)

        now = _now_iso()
        job = SynthesisJob(
            job_id=job_id,
            content_hash=content_hash,
            extractor=extractor.name,
            input_path=str(input_path),
            title=doc.title,
            sections_count=len(doc.sections),
            chunks=chunks,
            completed_chunks=set(),
            status=JobStatus.RUNNING,
            config=config.job_config(),
            output_path=str(resolved_output),
            created_at=now,
            updated_at=now,
        )
        self._store.save(job)
        return self._drive(job, config)

    def _resume(
        self,
        job: SynthesisJob,
        extractor: TextExtractor,
        config: Settings,
        output_path: Path | None,
    ) -> SynthesisJob:
        stored = Settings(**job.config)
        self._validate_config(config, stored, job)

        if job.status == JobStatus.COMPLETED:
            output = Path(job.output_path) if job.output_path else None
            if output is not None and output.exists():
                self._logger.info("job %s already completed; nothing to do", job.job_id)
                return job

        input_path = Path(job.input_path)
        if not input_path.exists():
            raise JobStateMismatchError(
                f"input file {input_path} no longer exists; cannot resume job {job.job_id}"
            )
        if content_hash_of_bytes(input_path.read_bytes()) != job.content_hash:
            raise JobStateMismatchError(
                f"input file {input_path} changed since job {job.job_id} was created"
            )

        doc, derived_chunks = self._extract_and_chunk(extractor, input_path, stored)
        self._log_warnings(doc)
        self._validate_chunks(derived_chunks, job.chunks)

        if output_path is not None:
            job.output_path = str(self._resolve_output(output_path, job.title))
        if job.output_path is None:
            job.output_path = str(self._resolve_output(None, job.title))

        job.status = JobStatus.RUNNING
        self._store.save(job)
        return self._drive(job, stored)

    def _drive(self, job: SynthesisJob, config: Settings) -> SynthesisJob:
        all_done = self._synthesize_chunks(job, config)
        if not all_done:
            return job
        self._merge_and_finalize(job, config)
        return job

    def _synthesize_chunks(self, job: SynthesisJob, config: Settings) -> bool:
        total = len(job.chunks)
        start = time.monotonic()
        deadline: float | None = None
        if config.max_runtime:
            deadline = start + parse_duration(config.max_runtime)

        for chunk in job.chunks:
            if chunk.index in job.completed_chunks:
                self._emit(job, chunk, total, start, "skipped")
                continue
            if self._stop_requested:
                job.status = JobStatus.PAUSED
                self._store.save(job)
                self._logger.warning("interrupted; paused. resume with: tts resume %s", job.job_id)
                return False
            if deadline is not None and time.monotonic() >= deadline:
                job.status = JobStatus.PAUSED
                self._store.save(job)
                self._logger.warning(
                    "max runtime reached; paused. resume with: tts resume %s", job.job_id
                )
                return False

            self._emit(job, chunk, total, start, "started")
            output_path = chunk_audio_path(self._temp_dir, job.job_id, chunk.index)
            try:
                self._synthesize_with_retry(chunk, output_path, config)
            except SynthesisError as exc:
                job.status = JobStatus.PAUSED
                self._store.save(job)
                self._logger.error("synthesis failed: %s", exc)
                raise SynthesisError(
                    f"chunk {chunk.index} failed after retries; job paused. "
                    f"resume with: tts resume {job.job_id}"
                ) from exc

            job.completed_chunks.add(chunk.index)
            self._store.save(job)
            self._emit(job, chunk, total, start, "completed")
            self._throttle.wait()

        return True

    def _synthesize_with_retry(self, chunk: Chunk, output_path: Path, config: Settings) -> None:
        attempt = 0
        while True:
            try:
                self._engine.synthesize(
                    chunk.text,
                    output_path,
                    voice=config.voice,
                    rate=config.rate,
                    timeout_seconds=config.chunk_timeout,
                )
                return
            except SynthesisError as exc:
                attempt += 1
                if attempt > config.retries:
                    raise SynthesisError(
                        f"chunk {chunk.index} failed after {config.retries} retries: {exc}"
                    ) from exc
                backoff = min(2**attempt, 60.0)
                self._logger.warning(
                    "chunk %d attempt %d failed (%s); retrying in %.1fs",
                    chunk.index,
                    attempt,
                    exc,
                    backoff,
                )
                time.sleep(backoff)

    def _merge_and_finalize(self, job: SynthesisJob, config: Settings) -> None:
        output = Path(job.output_path) if job.output_path else None
        if output is None:
            raise MergeError("no output path set")
        segments = [
            chunk_audio_path(self._temp_dir, job.job_id, chunk.index) for chunk in job.chunks
        ]
        missing = [segment.name for segment in segments if not segment.exists()]
        if missing:
            raise MergeError(f"missing audio for chunks: {missing}")

        self._merger.merge(
            segments,
            output,
            bitrate_kbps=config.bitrate_kbps,
            normalize=config.normalize,
            silence_between_ms=config.silence_between_ms,
        )
        shutil.rmtree(self._temp_dir / job.job_id, ignore_errors=True)
        job.status = JobStatus.COMPLETED
        self._store.save(job)
        self._logger.info("completed %s -> %s", job.job_id, output)

    def _extract_and_chunk(
        self, extractor: TextExtractor, input_path: Path, config: Settings
    ) -> tuple[SourceDocument, list[Chunk]]:
        doc = extractor.extract(input_path, password=config.pdf_password)
        chunks: list[Chunk] = []
        first_index = 0
        for section_index, section in enumerate(doc.sections):
            sentences = self._splitter.split(section.text, language=config.language)
            section_chunks = self._chunker.chunk(
                sentences,
                section_index=section_index,
                sentences_per_chunk=config.sentences_per_chunk,
                max_sentence_chars=config.max_sentence_chars,
                first_index=first_index,
            )
            chunks.extend(section_chunks)
            first_index += len(section_chunks)
        return doc, chunks

    def _validate_config(self, current: Settings, stored: Settings, job: SynthesisJob) -> None:
        current_id = make_job_id(
            job.content_hash,
            current.voice,
            current.rate,
            current.sentences_per_chunk,
            current.language,
        )
        if current_id != job.job_id:
            raise ConfigMismatchError(
                f"config mismatch for job {job.job_id}: job was created with "
                f"voice={stored.voice!r}, rate={stored.rate}, "
                f"sentences_per_chunk={stored.sentences_per_chunk}, "
                f"language={stored.language!r}; current config uses "
                f"voice={current.voice!r}, rate={current.rate}, "
                f"sentences_per_chunk={current.sentences_per_chunk}, "
                f"language={current.language!r}"
            )

    @staticmethod
    def _validate_chunks(derived: list[Chunk], stored: list[Chunk]) -> None:
        if len(derived) != len(stored):
            raise JobStateMismatchError(
                f"chunking changed ({len(derived)} vs {len(stored)} chunks); "
                "the input file may have changed"
            )
        for left, right in zip(derived, stored):
            if (
                left.index != right.index
                or left.section_index != right.section_index
                or left.sentence_indexes != right.sentence_indexes
                or left.text != right.text
            ):
                raise JobStateMismatchError(
                    "chunk boundaries changed since the job was created; "
                    "the input file may have changed"
                )

    def _emit(self, job: SynthesisJob, chunk: Chunk, total: int, start: float, status: str) -> None:
        if self._on_progress is None:
            return
        elapsed = time.monotonic() - start
        done = len(job.completed_chunks)
        remaining = total - done
        eta = (elapsed / done) * remaining if done > 0 else None
        self._on_progress(
            ProgressEvent(
                chunk_index=chunk.index,
                total_chunks=total,
                sentence_start=chunk.sentence_indexes[0] if chunk.sentence_indexes else 0,
                sentence_end=chunk.sentence_indexes[-1] if chunk.sentence_indexes else 0,
                chars=len(chunk.text),
                elapsed_seconds=elapsed,
                eta_seconds=eta,
                status=status,
            )
        )

    @staticmethod
    def _resolve_output(output_path: Path | None, title: str) -> Path:
        if output_path is not None:
            return output_path.expanduser().resolve()
        return Path(f"{_safe_filename(title)}.mp3").resolve()

    @staticmethod
    def _ensure_output_writable(output_path: Path) -> None:
        parent = output_path.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
            probe = parent / f".tts_write_probe_{os.getpid()}"
            probe.touch()
            probe.unlink()
        except OSError as exc:
            raise OutputNotWritableError(
                f"output path {output_path} is not writable: {exc}"
            ) from exc

    def _log_warnings(self, doc: SourceDocument) -> None:
        for warning in doc.warnings:
            self._logger.warning("%s", warning)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_filename(title: str) -> str:
    cleaned = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", " ", title)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned[:200] or "audiobook"
