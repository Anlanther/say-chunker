"""Pipeline orchestration: extract -> split -> chunk -> synthesize -> merge."""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from say_chunker.domain.errors import (
    EmptyDocumentError,
    ExtractionError,
    JobConfigMismatchError,
    JobNotFoundError,
    JobStateError,
    MergeError,
    OutputNotWritableError,
    TTSEngineError,
)
from say_chunker.domain.models import (
    AudioSegment,
    Chunk,
    ConversionResult,
    DocumentPlan,
    JobStatus,
    ProgressEvent,
    SourceDocument,
    SynthesisConfig,
    SynthesisJob,
)
from say_chunker.domain.ports import (
    AudioMerger,
    Chunker,
    JobStore,
    Planner,
    SentenceSplitter,
    TextExtractor,
    TTSEngine,
)
from say_chunker.pipeline.throttle import Cooldown
from say_chunker.utils.textutil import (
    ensure_writable_output,
    sanitize_filename,
    sha256_file,
    utc_now_iso,
)

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[ProgressEvent], None]


def compute_job_id(content_hash: str, config: SynthesisConfig) -> str:
    """Stable 16-hex job id from the content hash and plan-affecting settings."""
    payload = json.dumps(
        [
            content_hash,
            config.voice,
            config.rate,
            config.sentences_per_chunk,
            config.language,
        ],
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class DocumentPlanner:
    """Turn a SourceDocument into deterministic chunks, section by section."""

    def __init__(
        self,
        *,
        splitter: SentenceSplitter,
        chunker: Chunker,
        config: SynthesisConfig,
    ) -> None:
        self._splitter = splitter
        self._chunker = chunker
        self._config = config

    def plan(
        self,
        document: SourceDocument,
        *,
        config: SynthesisConfig | None = None,
    ) -> DocumentPlan:
        effective = config or self._config
        chunks: list[Chunk] = []
        sentence_count = 0
        speakable_count = 0
        section_count = 0

        for section_index, section in enumerate(document.sections):
            if not section.text.strip():
                continue
            section_count += 1
            sentences = self._splitter.split(section.text, language=effective.language)
            if not sentences:
                continue
            section_chunks = self._chunker.chunk(
                sentences,
                sentences_per_chunk=effective.sentences_per_chunk,
                max_sentence_chars=effective.max_sentence_chars,
                section_index=section_index,
                section_title=section.title,
                sentence_offset=sentence_count,
                chunk_offset=len(chunks),
            )
            chunks.extend(section_chunks)
            sentence_count += len(sentences)
            speakable_count += sum(len(chunk.sentences) for chunk in section_chunks)

        if not chunks:
            raise EmptyDocumentError(
                "No speakable text was found. If this is a scanned PDF, run "
                "`ocrmypdf input.pdf output.pdf` first, then convert the OCR'd PDF."
            )

        return DocumentPlan(
            chunks=tuple(chunks),
            sentence_count=sentence_count,
            speakable_sentence_count=speakable_count,
            section_count=section_count,
            warnings=tuple(document.warnings),
        )


class Orchestrator:
    """Drive a job. Dependencies are explicit; no adapter imports live here."""

    def __init__(
        self,
        *,
        planner: Planner,
        engine: TTSEngine,
        merger: AudioMerger,
        job_store: JobStore,
        config: SynthesisConfig,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._planner = planner
        self._engine = engine
        self._merger = merger
        self._job_store = job_store
        self._config = config
        self._clock = clock
        self._sleep = sleep

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def convert(
        self,
        input_path: Path,
        *,
        extractor: TextExtractor,
        output_path: Path | None = None,
        resume: bool = False,
        persist_input_path: bool = True,
        stop_event: threading.Event | None = None,
        progress: ProgressCallback | None = None,
    ) -> ConversionResult:
        started = self._clock()
        content_hash = _hash_input(input_path)
        job_id = compute_job_id(content_hash, self._config)
        requested_output = output_path.resolve() if output_path is not None else None

        if resume:
            existing = self._job_store.load(job_id)
            if existing is not None:
                return self._resume_existing(
                    existing,
                    extractor=extractor,
                    requested_output=requested_output,
                    current_config=self._config,
                    current_input_path=input_path,
                    persist_input_path=persist_input_path,
                    stop_event=stop_event,
                    progress=progress,
                    started=started,
                )
            logger.info("No existing job %s; starting a fresh conversion", job_id)

        document = extractor.extract(input_path)
        for warning in document.warnings:
            logger.warning("%s", warning)

        plan = self._planner.plan(document, config=self._config)
        effective_output = requested_output or _default_output_path(document.title)
        ensure_writable_output(effective_output)

        now = utc_now_iso()
        job = SynthesisJob(
            job_id=job_id,
            status=JobStatus.PAUSED,
            input_path=str(input_path.resolve()) if persist_input_path else None,
            content_hash=content_hash,
            extractor=extractor.name,
            output_path=str(effective_output),
            config=self._config,
            title=document.title,
            page_count=document.page_count,
            section_count=plan.section_count,
            sentence_count=plan.sentence_count,
            chunks=plan.chunks,
            completed_chunk_indices=frozenset(),
            warnings=plan.warnings,
            created_at=now,
            updated_at=now,
        )
        self._job_store.save(job)
        logger.info(
            "Created job %s: %d section(s), %d sentence(s), %d chunk(s)",
            job.job_id,
            plan.section_count,
            plan.sentence_count,
            len(plan.chunks),
        )
        return self._run_job(job, stop_event=stop_event, progress=progress, started=started)

    def resume(
        self,
        job_id: str,
        *,
        stop_event: threading.Event | None = None,
        progress: ProgressCallback | None = None,
    ) -> ConversionResult:
        started = self._clock()
        job = self._job_store.load(job_id)
        if job is None:
            raise JobNotFoundError(job_id)

        if not job.chunks:
            raise JobStateError(f"Job {job_id} has no chunks")

        if job.status == JobStatus.COMPLETED and Path(job.output_path).is_file():
            logger.info("Job %s is already complete: %s", job_id, job.output_path)
            return _noop_result(job)

        _verify_source_unchanged(job)
        ensure_writable_output(Path(job.output_path))
        return self._run_job(job, stop_event=stop_event, progress=progress, started=started)

    # ------------------------------------------------------------------
    # Resume validation
    # ------------------------------------------------------------------

    def _resume_existing(
        self,
        job: SynthesisJob,
        *,
        extractor: TextExtractor,
        requested_output: Path | None,
        current_config: SynthesisConfig,
        current_input_path: Path,
        persist_input_path: bool,
        stop_event: threading.Event | None,
        progress: ProgressCallback | None,
        started: float,
    ) -> ConversionResult:
        if extractor.name != job.extractor:
            raise JobConfigMismatchError(
                f"Job {job.job_id} was created with the {job.extractor!r} extractor, "
                f"but this run would use {extractor.name!r}. "
                "Run without --resume to start a new job."
            )

        if job.config.plan_signature() != current_config.plan_signature():
            raise JobConfigMismatchError(_plan_mismatch_message(job.config, current_config))

        if requested_output is not None and requested_output != Path(job.output_path).resolve():
            raise JobConfigMismatchError(
                f"Job {job.job_id} was created for output {job.output_path}, "
                f"but this run requested {requested_output}. "
                "Run without --resume to start a new job, or use the stored output path."
            )

        if job.status == JobStatus.COMPLETED and Path(job.output_path).is_file():
            logger.info("Job %s is already complete: %s", job.job_id, job.output_path)
            return _noop_result(job)

        # Runtime/merge settings (sleep, retries, normalize, bitrate, timeouts)
        # may legitimately change between invocations; adopt the current values.
        # Also update the stored source path, since an identical file may have
        # been moved between attempts.
        job = replace(job, config=current_config)
        if persist_input_path:
            job = replace(job, input_path=str(current_input_path.resolve()))
        _verify_source_unchanged(job)
        ensure_writable_output(Path(job.output_path))
        return self._run_job(job, stop_event=stop_event, progress=progress, started=started)

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def _run_job(
        self,
        job: SynthesisJob,
        *,
        stop_event: threading.Event | None,
        progress: ProgressCallback | None,
        started: float,
    ) -> ConversionResult:
        config = job.config
        total = len(job.chunks)
        if total == 0:
            raise JobStateError(f"Job {job.job_id} has no chunks")

        try:
            work_dir = self._job_store.work_dir(job.job_id)
            work_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise OutputNotWritableError(
                f"Cannot create job work directory for {job.job_id}: {exc}"
            ) from exc

        for stale in work_dir.glob(".say-input-*"):
            stale.unlink(missing_ok=True)

        completed = set(job.completed_chunk_indices)
        for index in list(completed):
            path = _chunk_audio_path(work_dir, index)
            if not path.is_file() or path.stat().st_size == 0:
                completed.discard(index)

        resumed_chunks = len(completed)
        if resumed_chunks:
            logger.info(
                "Resuming job %s with %d/%d chunk(s) already complete",
                job.job_id,
                resumed_chunks,
                total,
            )

        job = replace(
            job,
            status=JobStatus.RUNNING,
            completed_chunk_indices=frozenset(completed),
            updated_at=utc_now_iso(),
            last_error=None,
        )
        self._job_store.save(job)

        cooldown = Cooldown(config.sleep_between_chunks, sleep=self._sleep)
        synthesized = 0

        try:
            for chunk in job.chunks:
                if _stop_requested(stop_event):
                    return self._pause(
                        job,
                        completed,
                        synthesized,
                        started,
                        "Interrupted",
                    )

                if config.max_runtime_seconds is not None:
                    elapsed = self._clock() - started
                    if elapsed >= config.max_runtime_seconds:
                        return self._pause(
                            job,
                            completed,
                            synthesized,
                            started,
                            f"Maximum runtime of {config.max_runtime_seconds:g}s reached",
                        )

                if chunk.index in completed:
                    continue

                chunk_output = _chunk_audio_path(work_dir, chunk.index)
                chunk_started = self._clock()
                try:
                    self._synthesize_with_retries(
                        chunk,
                        chunk_output,
                        config=config,
                        stop_event=stop_event,
                    )
                except _ChunkFailed as exc:
                    return self._pause(
                        job,
                        completed,
                        synthesized,
                        started,
                        f"Chunk {chunk.index} failed after {config.retries + 1} attempt(s): {exc}",
                    )

                completed.add(chunk.index)
                synthesized += 1
                job = replace(
                    job,
                    status=JobStatus.RUNNING,
                    completed_chunk_indices=frozenset(completed),
                    updated_at=utc_now_iso(),
                )
                self._job_store.save(job)

                elapsed = self._clock() - started
                remaining = total - len(completed)
                average = (self._clock() - started) / synthesized if synthesized else None
                eta = average * remaining if average is not None and remaining else None
                logger.info(
                    "Chunk %d/%d complete: sentences %d-%d, %d chars, %.1fs",
                    chunk.index + 1,
                    total,
                    chunk.start_sentence_index,
                    chunk.end_sentence_index,
                    chunk.char_count,
                    self._clock() - chunk_started,
                )
                if progress is not None:
                    progress(
                        ProgressEvent(
                            job_id=job.job_id,
                            chunk_index=chunk.index,
                            total_chunks=total,
                            sentence_start=chunk.start_sentence_index,
                            sentence_end=chunk.end_sentence_index,
                            char_count=chunk.char_count,
                            elapsed_seconds=elapsed,
                            eta_seconds=eta,
                            chunks_remaining=remaining,
                        )
                    )

                if _stop_requested(stop_event):
                    return self._pause(
                        job,
                        completed,
                        synthesized,
                        started,
                        "Interrupted",
                    )

                if config.max_runtime_seconds is not None:
                    elapsed = self._clock() - started
                    if elapsed >= config.max_runtime_seconds:
                        return self._pause(
                            job,
                            completed,
                            synthesized,
                            started,
                            f"Maximum runtime of {config.max_runtime_seconds:g}s reached",
                        )

                if chunk.index != job.chunks[-1].index:
                    cooldown.wait()
        except KeyboardInterrupt:
            self._job_store.save(
                replace(
                    job,
                    status=JobStatus.PAUSED,
                    completed_chunk_indices=frozenset(completed),
                    updated_at=utc_now_iso(),
                    last_error="Interrupted",
                )
            )
            raise
        except Exception as exc:
            logger.exception("Unexpected failure while running job %s", job.job_id)
            self._job_store.save(
                replace(
                    job,
                    status=JobStatus.PAUSED,
                    completed_chunk_indices=frozenset(completed),
                    updated_at=utc_now_iso(),
                    last_error=str(exc),
                )
            )
            raise

        missing = [
            chunk.index
            for chunk in job.chunks
            if not _chunk_audio_path(work_dir, chunk.index).is_file()
        ]
        if missing:
            for index in missing:
                completed.discard(index)
            return self._pause(
                job,
                completed,
                synthesized,
                started,
                f"Audio is missing for chunk(s) {', '.join(map(str, missing))}",
            )

        segments = [
            AudioSegment(
                chunk_index=chunk.index,
                path=_chunk_audio_path(work_dir, chunk.index),
            )
            for chunk in job.chunks
        ]
        try:
            self._merger.merge(
                segments,
                Path(job.output_path),
                normalize=config.normalize,
                bitrate_kbps=config.bitrate_kbps,
                silence_ms=config.silence_between_chunks_ms,
            )
        except MergeError as exc:
            logger.error("Merging failed for job %s: %s", job.job_id, exc)
            return self._pause(
                job,
                completed,
                synthesized,
                started,
                f"Merging failed: {exc}",
            )

        completed_job = replace(
            job,
            status=JobStatus.COMPLETED,
            completed_chunk_indices=frozenset(range(total)),
            updated_at=utc_now_iso(),
            last_error=None,
        )
        self._job_store.save(completed_job)
        shutil.rmtree(work_dir, ignore_errors=True)
        logger.info(
            "Job %s complete: %s (%d chunk(s), %.1fs)",
            job.job_id,
            job.output_path,
            total,
            self._clock() - started,
        )

        return ConversionResult(
            job_id=job.job_id,
            status=JobStatus.COMPLETED,
            output_path=Path(job.output_path),
            total_chunks=total,
            completed_chunks=total,
            synthesized_chunks=synthesized,
            elapsed_seconds=self._clock() - started,
            message=f"Wrote {job.output_path}",
        )

    def _synthesize_with_retries(
        self,
        chunk: Chunk,
        output_path: Path,
        *,
        config: SynthesisConfig,
        stop_event: threading.Event | None,
    ) -> None:
        attempts = config.retries + 1
        last_error = ""
        for attempt in range(1, attempts + 1):
            output_path.unlink(missing_ok=True)
            try:
                self._engine.synthesize(
                    chunk,
                    output_path,
                    voice=config.voice,
                    rate=config.rate,
                    timeout_seconds=config.timeout_seconds,
                )
                return
            except TTSEngineError as exc:
                last_error = str(exc)
                logger.warning(
                    "Chunk %d attempt %d/%d failed: %s",
                    chunk.index,
                    attempt,
                    attempts,
                    exc,
                )
                if attempt >= attempts:
                    break
                backoff = min(30.0, 2.0 ** (attempt - 1))
                logger.info("Retrying chunk %d in %.1fs", chunk.index, backoff)
                self._sleep(backoff)
                if _stop_requested(stop_event):
                    raise _ChunkFailed("interrupted during retry backoff") from exc

        raise _ChunkFailed(last_error or "unknown synthesis failure")

    def _pause(
        self,
        job: SynthesisJob,
        completed: set[int],
        synthesized: int,
        started: float,
        reason: str,
    ) -> ConversionResult:
        paused = replace(
            job,
            status=JobStatus.PAUSED,
            completed_chunk_indices=frozenset(completed),
            updated_at=utc_now_iso(),
            last_error=reason,
        )
        self._job_store.save(paused)
        logger.warning("Job %s paused: %s", job.job_id, reason)
        return ConversionResult(
            job_id=job.job_id,
            status=JobStatus.PAUSED,
            output_path=Path(job.output_path),
            total_chunks=len(job.chunks),
            completed_chunks=len(completed),
            synthesized_chunks=synthesized,
            elapsed_seconds=self._clock() - started,
            message=f"{reason}. Resume with: tts resume {job.job_id}",
        )


class _ChunkFailed(Exception):
    pass


def _hash_input(path: Path) -> str:
    try:
        return sha256_file(path)
    except OSError as exc:
        raise ExtractionError(f"Could not read input file {path}: {exc}") from exc


def _default_output_path(title: str) -> Path:
    return Path.cwd() / f"{sanitize_filename(title)}.mp3"


def _chunk_audio_path(work_dir: Path, index: int) -> Path:
    return work_dir / f"chunk_{index:05d}.aiff"


def _stop_requested(stop_event: threading.Event | None) -> bool:
    return stop_event is not None and stop_event.is_set()


def _noop_result(job: SynthesisJob) -> ConversionResult:
    return ConversionResult(
        job_id=job.job_id,
        status=JobStatus.COMPLETED,
        output_path=Path(job.output_path),
        total_chunks=len(job.chunks),
        completed_chunks=len(job.chunks),
        synthesized_chunks=0,
        elapsed_seconds=0.0,
        message=f"Job already complete: {job.output_path}",
    )


def _verify_source_unchanged(job: SynthesisJob) -> None:
    if job.input_path is None:
        return
    path = Path(job.input_path)
    if not path.is_file():
        logger.warning(
            "Source file %s is no longer available; resuming from stored chunk text.",
            path,
        )
        return
    try:
        current_hash = sha256_file(path)
    except OSError as exc:
        logger.warning("Could not re-read source file %s (%s); resuming anyway.", path, exc)
        return
    if current_hash != job.content_hash:
        raise JobConfigMismatchError(
            f"Source file {path} has changed since job {job.job_id} was created. "
            "Start a new conversion instead of resuming this job."
        )


def _plan_mismatch_message(stored: SynthesisConfig, current: SynthesisConfig) -> str:
    stored_sig = stored.plan_signature()
    current_sig = current.plan_signature()
    labels = (
        "voice",
        "rate",
        "sentences_per_chunk",
        "language",
        "max_sentence_chars",
    )
    details = "\n".join(
        f"  {label}: {old!r} -> {new!r}"
        for label, old, new in zip(labels, stored_sig, current_sig, strict=True)
        if old != new
    )
    return (
        "The current settings do not match the stored job plan:\n"
        f"{details}\n"
        "Run without --resume to start a new job, or restore the original settings."
    )
