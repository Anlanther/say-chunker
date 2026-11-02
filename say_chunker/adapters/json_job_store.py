"""JSON-backed job state for crash-safe resume support."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from say_chunker.domain.errors import JobStateError, OutputNotWritableError
from say_chunker.domain.models import (
    Chunk,
    JobStatus,
    Sentence,
    SynthesisConfig,
    SynthesisJob,
)

logger = logging.getLogger(__name__)

JOB_ID_RE = re.compile(r"^[0-9a-f]{16}$")
SCHEMA_VERSION = 1


class JsonJobStore:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.jobs_dir = state_dir / "jobs"
        try:
            self.jobs_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise OutputNotWritableError(
                f"Cannot create state directory {self.jobs_dir}: {exc}"
            ) from exc

    def save(self, job: SynthesisJob) -> None:
        self._validate_id(job.job_id)
        path = self._json_path(job.job_id)
        payload = _job_to_dict(job)
        temp_name: str | None = None
        try:
            descriptor, temp_name = tempfile.mkstemp(
                prefix=f".{job.job_id}.", suffix=".tmp", dir=str(self.jobs_dir)
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, path)
            temp_name = None
        except OSError as exc:
            raise OutputNotWritableError(f"Cannot write job state {path}: {exc}") from exc
        finally:
            if temp_name is not None:
                Path(temp_name).unlink(missing_ok=True)

    def load(self, job_id: str) -> SynthesisJob | None:
        if not JOB_ID_RE.fullmatch(job_id):
            return None
        path = self._json_path(job_id)
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise JobStateError(f"Could not read job state {path}: {exc}") from exc
        try:
            job = _job_from_dict(raw)
        except (JobStateError, KeyError, TypeError, ValueError) as exc:
            raise JobStateError(f"Job state {path} is malformed: {exc}") from exc
        if job.job_id != job_id:
            raise JobStateError(
                f"Job state {path} contains job id {job.job_id!r}, "
                f"which does not match the filename."
            )
        return job

    def list_jobs(self) -> list[SynthesisJob]:
        jobs: list[SynthesisJob] = []
        for path in sorted(self.jobs_dir.glob("*.json")):
            job_id = path.stem
            if not JOB_ID_RE.fullmatch(job_id):
                continue
            try:
                job = self.load(job_id)
            except JobStateError as exc:
                logger.warning("Skipping unreadable job state %s: %s", path, exc)
                continue
            if job is not None:
                jobs.append(job)
        return sorted(jobs, key=lambda job: job.updated_at, reverse=True)

    def delete(self, job_id: str) -> None:
        if not JOB_ID_RE.fullmatch(job_id):
            return
        for path in (self._json_path(job_id), self.log_path(job_id)):
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Could not remove %s: %s", path, exc)
        try:
            shutil.rmtree(self.work_dir(job_id), ignore_errors=False)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("Could not remove work directory for %s: %s", job_id, exc)

    def work_dir(self, job_id: str) -> Path:
        self._validate_id(job_id)
        return self.jobs_dir / f"{job_id}.work"

    def log_path(self, job_id: str) -> Path:
        self._validate_id(job_id)
        return self.jobs_dir / f"{job_id}.log"

    def clean_older_than(self, seconds: float, *, now: float | None = None) -> list[str]:
        reference = time.time() if now is None else now
        cutoff = reference - seconds
        deleted: list[str] = []

        for path in sorted(self.jobs_dir.glob("*.json")):
            job_id = path.stem
            if not JOB_ID_RE.fullmatch(job_id):
                continue
            timestamp = path.stat().st_mtime
            try:
                job = self.load(job_id)
            except JobStateError:
                job = None
            if job is not None:
                timestamp = _timestamp(job.updated_at, fallback=timestamp)
            if timestamp <= cutoff:
                self.delete(job_id)
                deleted.append(job_id)

        for path in sorted(self.jobs_dir.iterdir()):
            if path.suffix not in {".log", ".work"}:
                continue
            job_id = path.name.split(".", maxsplit=1)[0]
            if not JOB_ID_RE.fullmatch(job_id):
                continue
            if self._json_path(job_id).exists():
                continue
            try:
                timestamp = path.stat().st_mtime
            except OSError:
                continue
            if timestamp <= cutoff:
                try:
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink(missing_ok=True)
                except OSError as exc:
                    logger.warning("Could not remove orphan %s: %s", path, exc)
                    continue
                deleted.append(job_id)

        return sorted(set(deleted))

    def _json_path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.json"

    @staticmethod
    def _validate_id(job_id: str) -> None:
        if not JOB_ID_RE.fullmatch(job_id):
            raise ValueError(f"Invalid job id: {job_id!r}")


def _timestamp(value: str, *, fallback: float) -> float:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def _job_to_dict(job: SynthesisJob) -> dict[str, Any]:
    return {
        "schema_version": job.schema_version,
        "job_id": job.job_id,
        "status": job.status.value,
        "input_path": job.input_path,
        "content_hash": job.content_hash,
        "extractor": job.extractor,
        "output_path": job.output_path,
        "title": job.title,
        "page_count": job.page_count,
        "section_count": job.section_count,
        "sentence_count": job.sentence_count,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "last_error": job.last_error,
        "warnings": list(job.warnings),
        "completed_chunk_indices": sorted(job.completed_chunk_indices),
        "config": {
            "voice": job.config.voice,
            "rate": job.config.rate,
            "sentences_per_chunk": job.config.sentences_per_chunk,
            "max_sentence_chars": job.config.max_sentence_chars,
            "language": job.config.language,
            "sleep_between_chunks": job.config.sleep_between_chunks,
            "retries": job.config.retries,
            "normalize": job.config.normalize,
            "bitrate_kbps": job.config.bitrate_kbps,
            "timeout_seconds": job.config.timeout_seconds,
            "silence_between_chunks_ms": job.config.silence_between_chunks_ms,
            "max_runtime_seconds": job.config.max_runtime_seconds,
        },
        "chunks": [_chunk_to_dict(chunk) for chunk in job.chunks],
    }


def _chunk_to_dict(chunk: Chunk) -> dict[str, Any]:
    return {
        "index": chunk.index,
        "section_index": chunk.section_index,
        "section_title": chunk.section_title,
        "start_sentence_index": chunk.start_sentence_index,
        "end_sentence_index": chunk.end_sentence_index,
        "char_count": chunk.char_count,
        "text": chunk.text,
        "sentences": [
            {"text": sentence.text, "paragraph_index": sentence.paragraph_index}
            for sentence in chunk.sentences
        ],
    }


def _job_from_dict(raw: object) -> SynthesisJob:
    data = _as_dict(raw, "job")
    schema_version = _as_int(data["schema_version"], "schema_version")
    if schema_version != SCHEMA_VERSION:
        raise JobStateError(
            f"Unsupported job schema version {schema_version}; expected {SCHEMA_VERSION}"
        )

    status_value = _as_str(data["status"], "status")
    try:
        status = JobStatus(status_value)
    except ValueError as exc:
        raise JobStateError(f"Unknown job status {status_value!r}") from exc

    config = _config_from_dict(_as_dict(data["config"], "config"))
    chunks = tuple(_chunk_from_dict(item) for item in _as_list(data["chunks"], "chunks"))
    if not chunks:
        raise JobStateError("Job has no chunks")
    expected_indices = list(range(len(chunks)))
    if [chunk.index for chunk in chunks] != expected_indices:
        raise JobStateError("Chunk indexes are not contiguous from zero")

    completed = frozenset(
        _as_int(value, "completed_chunk_indices")
        for value in _as_list(data["completed_chunk_indices"], "completed_chunk_indices")
    )
    if any(index < 0 or index >= len(chunks) for index in completed):
        raise JobStateError("Completed chunk indexes are outside the chunk list")

    warnings = tuple(_as_str(value, "warnings") for value in _as_list(data["warnings"], "warnings"))

    return SynthesisJob(
        job_id=_as_str(data["job_id"], "job_id"),
        status=status,
        input_path=_as_opt_str(data.get("input_path"), "input_path"),
        content_hash=_as_str(data["content_hash"], "content_hash"),
        extractor=_as_str(data["extractor"], "extractor"),
        output_path=_as_str(data["output_path"], "output_path"),
        config=config,
        title=_as_str(data["title"], "title"),
        page_count=_as_int(data["page_count"], "page_count"),
        section_count=_as_int(data["section_count"], "section_count"),
        sentence_count=_as_int(data["sentence_count"], "sentence_count"),
        chunks=chunks,
        completed_chunk_indices=completed,
        warnings=warnings,
        created_at=_as_str(data["created_at"], "created_at"),
        updated_at=_as_str(data["updated_at"], "updated_at"),
        schema_version=schema_version,
        last_error=_as_opt_str(data.get("last_error"), "last_error"),
    )


def _config_from_dict(data: dict[str, Any]) -> SynthesisConfig:
    return SynthesisConfig(
        voice=_as_str(data["voice"], "config.voice"),
        rate=_as_int(data["rate"], "config.rate"),
        sentences_per_chunk=_as_int(data["sentences_per_chunk"], "config.sentences_per_chunk"),
        max_sentence_chars=_as_int(data["max_sentence_chars"], "config.max_sentence_chars"),
        language=_as_str(data["language"], "config.language"),
        sleep_between_chunks=_as_float(data["sleep_between_chunks"], "config.sleep_between_chunks"),
        retries=_as_int(data["retries"], "config.retries"),
        normalize=_as_bool(data["normalize"], "config.normalize"),
        bitrate_kbps=_as_int(data["bitrate_kbps"], "config.bitrate_kbps"),
        timeout_seconds=_as_float(data["timeout_seconds"], "config.timeout_seconds"),
        silence_between_chunks_ms=_as_int(
            data["silence_between_chunks_ms"], "config.silence_between_chunks_ms"
        ),
        max_runtime_seconds=_as_opt_float(
            data.get("max_runtime_seconds"), "config.max_runtime_seconds"
        ),
    )


def _chunk_from_dict(raw: object) -> Chunk:
    data = _as_dict(raw, "chunk")
    sentences = tuple(
        Sentence(
            text=_as_str(_as_dict(item, "sentence")["text"], "sentence.text"),
            paragraph_index=_as_int(
                _as_dict(item, "sentence")["paragraph_index"], "sentence.paragraph_index"
            ),
        )
        for item in _as_list(data["sentences"], "chunk.sentences")
    )
    return Chunk(
        index=_as_int(data["index"], "chunk.index"),
        section_index=_as_int(data["section_index"], "chunk.section_index"),
        section_title=_as_opt_str(data.get("section_title"), "chunk.section_title"),
        sentences=sentences,
        start_sentence_index=_as_int(data["start_sentence_index"], "chunk.start_sentence_index"),
        end_sentence_index=_as_int(data["end_sentence_index"], "chunk.end_sentence_index"),
        text=_as_str(data["text"], "chunk.text"),
        char_count=_as_int(data["char_count"], "chunk.char_count"),
    )


def _as_dict(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise JobStateError(f"{field} must be an object")
    result: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise JobStateError(f"{field} has a non-string key")
        result[key] = item
    return result


def _as_list(value: object, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise JobStateError(f"{field} must be a list")
    return value


def _as_str(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise JobStateError(f"{field} must be a string")
    return value


def _as_opt_str(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _as_str(value, field)


def _as_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise JobStateError(f"{field} must be an integer")
    return value


def _as_float(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise JobStateError(f"{field} must be a number")
    return float(value)


def _as_opt_float(value: object, field: str) -> float | None:
    if value is None:
        return None
    return _as_float(value, field)


def _as_bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise JobStateError(f"{field} must be a boolean")
    return value
