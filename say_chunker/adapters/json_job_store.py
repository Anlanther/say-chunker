"""JSON-backed job store for resume support."""

from __future__ import annotations

import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from say_chunker.domain.models import Chunk, JobStatus, SynthesisJob


class JSONJobStore:
    def __init__(self, jobs_dir: Path, *, temp_dir: Path | None = None) -> None:
        self.jobs_dir = jobs_dir
        self.temp_dir = temp_dir
        self.jobs_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.json"

    def save(self, job: SynthesisJob) -> None:
        job.updated_at = _now()
        self._path(job.job_id).write_text(
            json.dumps(_serialize_job(job), indent=2), encoding="utf-8"
        )

    def load(self, job_id: str) -> SynthesisJob | None:
        path = self._path(job_id)
        if not path.exists():
            return None
        return _deserialize_job(json.loads(path.read_text(encoding="utf-8")))

    def list_jobs(self) -> list[SynthesisJob]:
        jobs: list[SynthesisJob] = []
        for path in sorted(self.jobs_dir.glob("*.json")):
            try:
                jobs.append(_deserialize_job(json.loads(path.read_text(encoding="utf-8"))))
            except (json.JSONDecodeError, KeyError):
                continue
        return jobs

    def delete(self, job_id: str) -> None:
        self._path(job_id).unlink(missing_ok=True)
        if self.temp_dir is not None:
            shutil.rmtree(self.temp_dir / job_id, ignore_errors=True)

    def clean(self, *, older_than_days: float | None) -> int:
        removed = 0
        now = time.time()
        for path in self.jobs_dir.glob("*.json"):
            if older_than_days is not None:
                age_days = (now - path.stat().st_mtime) / 86400.0
                if age_days <= older_than_days:
                    continue
            job_id = path.stem
            path.unlink(missing_ok=True)
            if self.temp_dir is not None:
                shutil.rmtree(self.temp_dir / job_id, ignore_errors=True)
            removed += 1
        return removed


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _serialize_job(job: SynthesisJob) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "content_hash": job.content_hash,
        "extractor": job.extractor,
        "input_path": job.input_path,
        "title": job.title,
        "sections_count": job.sections_count,
        "chunks": [
            {
                "index": chunk.index,
                "section_index": chunk.section_index,
                "sentence_indexes": list(chunk.sentence_indexes),
                "text": chunk.text,
            }
            for chunk in job.chunks
        ],
        "completed_chunks": sorted(job.completed_chunks),
        "status": job.status.value,
        "config": job.config,
        "output_path": job.output_path,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def _deserialize_job(data: dict[str, Any]) -> SynthesisJob:
    return SynthesisJob(
        job_id=str(data["job_id"]),
        content_hash=str(data["content_hash"]),
        extractor=str(data["extractor"]),
        input_path=str(data["input_path"]),
        title=str(data["title"]),
        sections_count=int(data["sections_count"]),
        chunks=[
            Chunk(
                index=int(item["index"]),
                section_index=int(item["section_index"]),
                sentence_indexes=[int(i) for i in item["sentence_indexes"]],
                text=str(item["text"]),
            )
            for item in data["chunks"]
        ],
        completed_chunks={int(i) for i in data["completed_chunks"]},
        status=JobStatus(str(data["status"])),
        config=dict(data["config"]),
        output_path=str(data["output_path"]) if data.get("output_path") is not None else None,
        created_at=str(data["created_at"]),
        updated_at=str(data.get("updated_at", "")),
    )
