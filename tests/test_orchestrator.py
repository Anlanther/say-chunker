"""Integration tests for the orchestrator: pipeline + resume."""

from __future__ import annotations

from pathlib import Path

import pytest

from say_chunker.adapters.extractors.txt_extractor import TxtExtractor
from say_chunker.adapters.json_job_store import JSONJobStore
from say_chunker.adapters.pysbd_splitter import PySBDSentenceSplitter
from say_chunker.config import Settings
from say_chunker.domain.errors import ConfigMismatchError, SynthesisError
from say_chunker.domain.models import JobStatus
from say_chunker.pipeline.chunker import SentenceChunker
from say_chunker.pipeline.orchestrator import Orchestrator, content_hash_of_bytes, make_job_id
from say_chunker.pipeline.throttle import Throttle


class MockEngine:
    def __init__(self, fail_chunks: set[int] | None = None) -> None:
        self.calls: list[str] = []
        self.synthesized: list[int] = []
        self.fail_chunks = fail_chunks or set()

    def synthesize(
        self,
        text: str,
        output_path: Path,
        *,
        voice: str,
        rate: int,
        timeout_seconds: float,
    ) -> Path:
        index = int(output_path.stem.split("_")[1])
        if index in self.fail_chunks:
            raise SynthesisError(f"boom chunk {index}")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"FAKEAIFF")
        self.calls.append(text)
        self.synthesized.append(index)
        return output_path


class MockMerger:
    def __init__(self) -> None:
        self.calls: list[tuple[list[Path], Path]] = []

    def merge(
        self,
        segments: list[Path],
        output_path: Path,
        *,
        bitrate_kbps: int,
        normalize: bool,
        silence_between_ms: int,
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"FAKEMP3")
        self.calls.append((list(segments), output_path))
        return output_path


def _write_txt(path: Path, n: int = 20) -> None:
    path.write_text(" ".join(f"Sentence {i} ends here." for i in range(n)), encoding="utf-8")


def _orchestrator(
    tmp_path: Path,
    engine: MockEngine,
    merger: MockMerger,
    config: Settings,
    store: JSONJobStore,
) -> Orchestrator:
    return Orchestrator(
        splitter=PySBDSentenceSplitter(),
        chunker=SentenceChunker(),
        engine=engine,
        merger=merger,
        store=store,
        throttle=Throttle(0.0),
        temp_dir=tmp_path / "tmp",
    )


def test_end_to_end_no_chunk_exceeds_sentence_count(tmp_path: Path) -> None:
    input_txt = tmp_path / "book.txt"
    _write_txt(input_txt, 20)

    engine = MockEngine()
    merger = MockMerger()
    config = Settings(sentences_per_chunk=5)
    store = JSONJobStore(tmp_path / "jobs", temp_dir=tmp_path / "tmp")
    orchestrator = _orchestrator(tmp_path, engine, merger, config, store)

    job = orchestrator.run(
        extractor=TxtExtractor(),
        input_path=input_txt,
        output_path=tmp_path / "out.mp3",
        config=config,
        content_hash=content_hash_of_bytes(input_txt.read_bytes()),
        resume=False,
    )

    assert job.status == JobStatus.COMPLETED
    assert len(engine.calls) == 4
    splitter = PySBDSentenceSplitter()
    for text in engine.calls:
        assert len(splitter.split(text, language="en")) <= 5
    assert (tmp_path / "out.mp3").exists()
    assert merger.calls


def test_resume_completes_remaining_chunks(tmp_path: Path) -> None:
    input_txt = tmp_path / "book.txt"
    _write_txt(input_txt, 20)

    config = Settings(sentences_per_chunk=5)
    content_hash = content_hash_of_bytes(input_txt.read_bytes())
    job_id = make_job_id(
        content_hash, config.voice, config.rate, config.sentences_per_chunk, config.language
    )
    store = JSONJobStore(tmp_path / "jobs", temp_dir=tmp_path / "tmp")

    # First run: chunk 2 fails persistently (retries exhausted -> paused).
    failing = MockEngine(fail_chunks={2})
    orchestrator = _orchestrator(tmp_path, failing, MockMerger(), config, store)
    with pytest.raises(SynthesisError):
        orchestrator.run(
            extractor=TxtExtractor(),
            input_path=input_txt,
            output_path=tmp_path / "out.mp3",
            config=config,
            content_hash=content_hash,
            resume=False,
        )

    saved = store.load(job_id)
    assert saved is not None
    assert saved.status == JobStatus.PAUSED
    assert saved.completed_chunks == {0, 1}

    # Resume with a healthy engine: only chunks 2 and 3 are synthesized.
    good = MockEngine()
    orchestrator2 = _orchestrator(tmp_path, good, MockMerger(), config, store)
    job = orchestrator2.resume(job_id=job_id, extractor=TxtExtractor(), config=config)

    assert job.status == JobStatus.COMPLETED
    assert good.synthesized == [2, 3]
    assert (tmp_path / "out.mp3").exists()


def test_resume_idempotent(tmp_path: Path) -> None:
    input_txt = tmp_path / "book.txt"
    _write_txt(input_txt, 20)

    config = Settings(sentences_per_chunk=5)
    content_hash = content_hash_of_bytes(input_txt.read_bytes())
    job_id = make_job_id(
        content_hash, config.voice, config.rate, config.sentences_per_chunk, config.language
    )
    store = JSONJobStore(tmp_path / "jobs", temp_dir=tmp_path / "tmp")

    orchestrator = _orchestrator(tmp_path, MockEngine(), MockMerger(), config, store)
    orchestrator.run(
        extractor=TxtExtractor(),
        input_path=input_txt,
        output_path=tmp_path / "out.mp3",
        config=config,
        content_hash=content_hash,
        resume=False,
    )

    good = MockEngine()
    orchestrator2 = _orchestrator(tmp_path, good, MockMerger(), config, store)
    job = orchestrator2.resume(job_id=job_id, extractor=TxtExtractor(), config=config)

    assert job.status == JobStatus.COMPLETED
    assert good.synthesized == []  # nothing duplicated


def test_resume_config_mismatch(tmp_path: Path) -> None:
    input_txt = tmp_path / "book.txt"
    _write_txt(input_txt, 20)

    config = Settings(sentences_per_chunk=5)
    content_hash = content_hash_of_bytes(input_txt.read_bytes())
    store = JSONJobStore(tmp_path / "jobs", temp_dir=tmp_path / "tmp")

    orchestrator = _orchestrator(tmp_path, MockEngine(), MockMerger(), config, store)
    orchestrator.run(
        extractor=TxtExtractor(),
        input_path=input_txt,
        output_path=tmp_path / "out.mp3",
        config=config,
        content_hash=content_hash,
        resume=False,
    )

    job_id = make_job_id(
        content_hash, config.voice, config.rate, config.sentences_per_chunk, config.language
    )
    with pytest.raises(ConfigMismatchError):
        orchestrator.resume(
            job_id=job_id,
            extractor=TxtExtractor(),
            config=Settings(sentences_per_chunk=6),
        )


def test_resume_without_existing_job_runs_fresh(tmp_path: Path) -> None:
    input_txt = tmp_path / "book.txt"
    _write_txt(input_txt, 20)

    engine = MockEngine()
    config = Settings(sentences_per_chunk=5)
    store = JSONJobStore(tmp_path / "jobs", temp_dir=tmp_path / "tmp")
    orchestrator = _orchestrator(tmp_path, engine, MockMerger(), config, store)

    job = orchestrator.run(
        extractor=TxtExtractor(),
        input_path=input_txt,
        output_path=tmp_path / "out.mp3",
        config=config,
        content_hash=content_hash_of_bytes(input_txt.read_bytes()),
        resume=True,  # no existing job -> fresh
    )
    assert job.status == JobStatus.COMPLETED
    assert len(engine.calls) == 4


def test_job_id_determinism_and_sensitivity() -> None:
    content_hash = "abc123"
    base = make_job_id(content_hash, "Samantha", 200, 5, "en")
    assert base == make_job_id(content_hash, "Samantha", 200, 5, "en")
    assert base != make_job_id(content_hash, "Samantha", 200, 6, "en")
    assert base != make_job_id(content_hash, "Alex", 200, 5, "en")
    assert base != make_job_id("different", "Samantha", 200, 5, "en")


def test_content_hash() -> None:
    assert content_hash_of_bytes(b"hello") == content_hash_of_bytes(b"hello")
    assert content_hash_of_bytes(b"hello") != content_hash_of_bytes(b"world")
