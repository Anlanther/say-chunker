from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from say_chunker.adapters.json_job_store import JsonJobStore
from say_chunker.domain.errors import JobStateError
from say_chunker.domain.models import JobStatus
from say_chunker.utils.textutil import utc_now_iso
from tests.factories import make_job


def test_round_trip(tmp_path: Path) -> None:
    store = JsonJobStore(tmp_path / "state")
    job = make_job(
        status=JobStatus.PAUSED,
        completed=frozenset({0}),
    )
    store.save(job)
    loaded = store.load(job.job_id)
    assert loaded == job


def test_atomic_write_leaves_no_temp_files(tmp_path: Path) -> None:
    store = JsonJobStore(tmp_path / "state")
    store.save(make_job())
    assert not list(store.jobs_dir.glob("*.tmp"))
    assert (store.jobs_dir / f"{make_job().job_id}.json").is_file()


def test_load_missing_or_invalid_id(tmp_path: Path) -> None:
    store = JsonJobStore(tmp_path / "state")
    assert store.load("0123456789abcdef") is None
    assert store.load("../etc/passwd") is None
    assert store.load("not-a-job-id") is None


def test_save_invalid_id(tmp_path: Path) -> None:
    store = JsonJobStore(tmp_path / "state")
    with pytest.raises(ValueError, match="Invalid job id"):
        store.save(make_job(job_id="NOPE"))


def test_malformed_json_raises(tmp_path: Path) -> None:
    store = JsonJobStore(tmp_path / "state")
    path = store.jobs_dir / "0123456789abcdef.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(JobStateError, match="Could not read job state"):
        store.load("0123456789abcdef")


def test_unsupported_schema_version_raises(tmp_path: Path) -> None:
    store = JsonJobStore(tmp_path / "state")
    job = make_job()
    store.save(job)
    path = store.jobs_dir / f"{job.job_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema_version"] = 99
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(JobStateError, match="schema version"):
        store.load(job.job_id)


def test_out_of_range_completed_indices_raise(tmp_path: Path) -> None:
    store = JsonJobStore(tmp_path / "state")
    job = make_job()
    store.save(job)
    path = store.jobs_dir / f"{job.job_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["completed_chunk_indices"] = [99]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(JobStateError, match="outside the chunk list"):
        store.load(job.job_id)


def test_filename_job_id_mismatch_raises(tmp_path: Path) -> None:
    store = JsonJobStore(tmp_path / "state")
    job = make_job()
    store.save(job)
    path = store.jobs_dir / f"{job.job_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["job_id"] = "ffffffffffffffff"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(JobStateError, match="does not match the filename"):
        store.load(job.job_id)


def test_empty_chunk_list_raises(tmp_path: Path) -> None:
    store = JsonJobStore(tmp_path / "state")
    job = make_job()
    store.save(job)
    path = store.jobs_dir / f"{job.job_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["chunks"] = []
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(JobStateError, match="no chunks"):
        store.load(job.job_id)


def test_list_jobs_sorted_by_updated_at(tmp_path: Path) -> None:
    store = JsonJobStore(tmp_path / "state")
    old = make_job(
        job_id="1111111111111111",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    new = make_job(
        job_id="2222222222222222",
        updated_at="2026-02-01T00:00:00+00:00",
    )
    store.save(old)
    store.save(new)
    jobs = store.list_jobs()
    assert [job.job_id for job in jobs] == [new.job_id, old.job_id]


def test_delete_removes_json_log_and_work_dir(tmp_path: Path) -> None:
    store = JsonJobStore(tmp_path / "state")
    job = make_job()
    store.save(job)
    store.log_path(job.job_id).write_text("log", encoding="utf-8")
    store.work_dir(job.job_id).mkdir()
    (store.work_dir(job.job_id) / "chunk.aiff").write_bytes(b"x")

    store.delete(job.job_id)
    assert store.load(job.job_id) is None
    assert not store.log_path(job.job_id).exists()
    assert not store.work_dir(job.job_id).exists()


def test_clean_older_than(tmp_path: Path) -> None:
    store = JsonJobStore(tmp_path / "state")
    old = make_job(
        job_id="1111111111111111",
        updated_at="2020-01-01T00:00:00+00:00",
    )
    new = make_job(
        job_id="2222222222222222",
        updated_at=utc_now_iso(),
    )
    store.save(old)
    store.save(new)

    deleted = store.clean_older_than(86400, now=time.time())
    assert old.job_id in deleted
    assert new.job_id not in deleted
    assert store.load(old.job_id) is None
    assert store.load(new.job_id) is not None


def test_clean_removes_orphan_logs_and_work_dirs(tmp_path: Path) -> None:
    store = JsonJobStore(tmp_path / "state")
    job_id = "3333333333333333"
    log_path = store.log_path(job_id)
    work_dir = store.work_dir(job_id)
    log_path.write_text("old log", encoding="utf-8")
    work_dir.mkdir()
    old_time = time.time() - 30 * 86400
    import os

    os.utime(log_path, (old_time, old_time))
    os.utime(work_dir, (old_time, old_time))

    deleted = store.clean_older_than(7 * 86400)
    assert job_id in deleted
    assert not log_path.exists()
    assert not work_dir.exists()
