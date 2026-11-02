from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from say_chunker.adapters.extractors import MarkdownExtractor, TxtExtractor
from say_chunker.adapters.json_job_store import JsonJobStore
from say_chunker.adapters.pysbd_splitter import PysbdSentenceSplitter
from say_chunker.domain.errors import (
    EmptyDocumentError,
    JobConfigMismatchError,
    MergeError,
    SynthesisError,
)
from say_chunker.domain.models import (
    AudioSegment,
    Chunk,
    JobStatus,
    ProgressEvent,
    Section,
    SourceDocument,
    SynthesisConfig,
)
from say_chunker.pipeline.chunker import SentenceCountChunker
from say_chunker.pipeline.orchestrator import (
    DocumentPlanner,
    Orchestrator,
    compute_job_id,
)
from say_chunker.utils.textutil import sha256_file
from tests.factories import make_config


class FakeEngine:
    name = "fake"

    def __init__(
        self,
        *,
        failures: dict[int, int] | None = None,
        on_success: Callable[[Chunk], None] | None = None,
    ) -> None:
        self.failures = dict(failures or {})
        self.on_success = on_success
        self.attempted: list[int] = []
        self.succeeded: list[int] = []
        self.calls: list[tuple[int, str, int]] = []

    def list_voices(self) -> list[str]:
        return ["Samantha"]

    def validate_voice(self, voice: str) -> None:
        if voice != "Samantha":
            raise AssertionError(f"unexpected voice {voice}")

    def synthesize(
        self,
        chunk: Chunk,
        output_path: Path,
        *,
        voice: str,
        rate: int,
        timeout_seconds: float,
    ) -> AudioSegment:
        self.attempted.append(chunk.index)
        self.calls.append((chunk.index, voice, rate))
        remaining = self.failures.get(chunk.index, 0)
        if remaining > 0:
            self.failures[chunk.index] = remaining - 1
            raise SynthesisError(f"simulated failure for chunk {chunk.index}")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(chunk.text, encoding="utf-8")
        self.succeeded.append(chunk.index)
        if self.on_success is not None:
            self.on_success(chunk)
        return AudioSegment(chunk_index=chunk.index, path=output_path)


class FakeMerger:
    name = "fake-merger"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[list[int], bool, int, int]] = []

    def validate(self) -> None:
        return None

    def merge(
        self,
        segments: list[AudioSegment],
        output_path: Path,
        *,
        normalize: bool,
        bitrate_kbps: int,
        silence_ms: int,
    ) -> None:
        self.calls.append(
            ([segment.chunk_index for segment in segments], normalize, bitrate_kbps, silence_ms)
        )
        if self.fail:
            raise MergeError("simulated merge failure")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            "\n\n".join(segment.path.read_text(encoding="utf-8") for segment in segments),
            encoding="utf-8",
        )


class FakeClock:
    def __init__(self, step: float = 0.0) -> None:
        self.value = 0.0
        self.step = step

    def __call__(self) -> float:
        self.value += self.step
        return self.value


def build_orchestrator(
    tmp_path: Path,
    *,
    config: SynthesisConfig | None = None,
    engine: FakeEngine | None = None,
    merger: FakeMerger | None = None,
    sleep: Callable[[float], None] | None = None,
    clock: Callable[[], float] | None = None,
) -> tuple[Orchestrator, JsonJobStore, SynthesisConfig, FakeEngine, FakeMerger]:
    effective_config = config or make_config(sleep_between_chunks=0.0)
    store = JsonJobStore(tmp_path / "state")
    planner = DocumentPlanner(
        splitter=PysbdSentenceSplitter(),
        chunker=SentenceCountChunker(),
        config=effective_config,
    )
    effective_engine = engine or FakeEngine()
    effective_merger = merger or FakeMerger()
    orchestrator = Orchestrator(
        planner=planner,
        engine=effective_engine,
        merger=effective_merger,
        job_store=store,
        config=effective_config,
        clock=clock or time.monotonic,
        sleep=sleep or (lambda _: None),
    )
    return orchestrator, store, effective_config, effective_engine, effective_merger


def write_input(tmp_path: Path, paragraphs: list[str]) -> Path:
    path = tmp_path / "input.txt"
    path.write_text("\n\n".join(paragraphs) + "\n", encoding="utf-8")
    return path


def paragraph(letter: str, count: int = 4) -> str:
    return " ".join(f"{letter}{index} sentence." for index in range(1, count + 1))


def test_convert_end_to_end_and_sentence_limits(tmp_path: Path) -> None:
    input_path = write_input(tmp_path, [paragraph("A"), paragraph("B")])
    output_path = tmp_path / "book.mp3"
    sleeps: list[float] = []
    orchestrator, store, _config, engine, merger = build_orchestrator(
        tmp_path,
        config=make_config(sentences_per_chunk=3, sleep_between_chunks=0.5),
        sleep=sleeps.append,
    )

    result = orchestrator.convert(
        input_path,
        extractor=TxtExtractor(),
        output_path=output_path,
    )

    assert result.status == JobStatus.COMPLETED
    assert result.total_chunks == 4
    assert result.completed_chunks == 4
    assert result.synthesized_chunks == 4
    assert output_path.is_file()
    assert all(len(chunk.sentences) <= 3 for chunk in engine_calls_to_chunks(store, result.job_id))
    assert [call[0] for call in engine.calls] == [0, 1, 2, 3]
    assert all(call[1:] == ("Samantha", 200) for call in engine.calls)
    assert merger.calls == [([0, 1, 2, 3], True, 128, 250)]
    assert sleeps == [0.5, 0.5, 0.5]
    assert not store.work_dir(result.job_id).exists()

    job = store.load(result.job_id)
    assert job is not None
    assert job.status == JobStatus.COMPLETED
    assert job.completed_chunk_indices == frozenset(range(4))


def test_default_sentence_limit_is_enforced_by_mock_engine(tmp_path: Path) -> None:
    input_path = write_input(tmp_path, [paragraph("A", 12)])
    output_path = tmp_path / "book.mp3"
    orchestrator, store, config, engine, _ = build_orchestrator(tmp_path)
    assert config.sentences_per_chunk == 5

    result = orchestrator.convert(input_path, extractor=TxtExtractor(), output_path=output_path)
    assert result.status == JobStatus.COMPLETED
    job = store.load(result.job_id)
    assert job is not None
    assert [chunk.sentence_count for chunk in job.chunks] == [5, 5, 2]
    assert all(chunk.sentence_count <= 5 for chunk in job.chunks)
    assert engine.attempted == [0, 1, 2]


def test_chunk_boundaries_are_deterministic(tmp_path: Path) -> None:
    input_path = write_input(tmp_path, [paragraph("A", 7), paragraph("B", 7)])
    document = TxtExtractor().extract(input_path)
    config = make_config(sentences_per_chunk=5)
    planner_one = DocumentPlanner(
        splitter=PysbdSentenceSplitter(),
        chunker=SentenceCountChunker(),
        config=config,
    )
    planner_two = DocumentPlanner(
        splitter=PysbdSentenceSplitter(),
        chunker=SentenceCountChunker(),
        config=config,
    )
    first = planner_one.plan(document)
    second = planner_two.plan(document)
    assert first == second
    assert compute_job_id("abc", config) == compute_job_id("abc", config)


def test_failure_pauses_and_resume_completes_same_as_uninterrupted(tmp_path: Path) -> None:
    input_path = write_input(tmp_path, [paragraph(letter, 2) for letter in "ABCDEF"])
    config = make_config(sentences_per_chunk=5, retries=0, sleep_between_chunks=0.0)

    # Baseline uninterrupted run in its own state directory.
    baseline_output = tmp_path / "baseline.mp3"
    baseline_orchestrator, baseline_store, _, baseline_engine, _ = build_orchestrator(
        tmp_path / "baseline", config=config
    )
    baseline_result = baseline_orchestrator.convert(
        input_path,
        extractor=TxtExtractor(),
        output_path=baseline_output,
    )
    assert baseline_result.status == JobStatus.COMPLETED
    assert [index for index, _, _ in baseline_engine.calls] == list(range(6))

    # Interrupted run: chunk 2 fails permanently.
    failing_engine = FakeEngine(failures={2: 999})
    output_path = tmp_path / "resumed.mp3"
    orchestrator, store, _, _, merger = build_orchestrator(
        tmp_path / "resumed",
        config=config,
        engine=failing_engine,
    )
    paused = orchestrator.convert(
        input_path,
        extractor=TxtExtractor(),
        output_path=output_path,
    )
    assert paused.status == JobStatus.PAUSED
    assert paused.completed_chunks == 2
    assert "tts resume" in (paused.message or "")
    assert failing_engine.attempted == [0, 1, 2]
    assert not output_path.exists()
    job = store.load(paused.job_id)
    assert job is not None
    assert job.status == JobStatus.PAUSED
    assert job.completed_chunk_indices == frozenset({0, 1})
    assert store.work_dir(paused.job_id).is_dir()
    assert merger.calls == []

    # Resume with a working engine.
    success_engine = FakeEngine()
    resumed_orchestrator, _, _, _, resumed_merger = build_orchestrator(
        tmp_path / "resumed",
        config=config,
        engine=success_engine,
    )
    resumed = resumed_orchestrator.resume(paused.job_id)
    assert resumed.status == JobStatus.COMPLETED
    assert resumed.synthesized_chunks == 4
    assert success_engine.attempted == [2, 3, 4, 5]
    assert output_path.is_file()
    assert output_path.read_text(encoding="utf-8") == baseline_output.read_text(encoding="utf-8")
    assert resumed_merger.calls and resumed_merger.calls[0][0] == [0, 1, 2, 3, 4, 5]

    final_job = store.load(paused.job_id)
    assert final_job is not None
    assert final_job.status == JobStatus.COMPLETED
    assert final_job.completed_chunk_indices == frozenset(range(6))
    assert not store.work_dir(paused.job_id).exists()
    assert not baseline_store.work_dir(baseline_result.job_id).exists()


def test_resume_completed_job_is_idempotent(tmp_path: Path) -> None:
    input_path = write_input(tmp_path, [paragraph("A", 2)])
    output_path = tmp_path / "book.mp3"
    orchestrator, store, config, engine, _ = build_orchestrator(tmp_path)
    result = orchestrator.convert(input_path, extractor=TxtExtractor(), output_path=output_path)
    assert result.status == JobStatus.COMPLETED
    original = output_path.read_bytes()
    calls_before = list(engine.attempted)

    again_engine = FakeEngine()
    again, _, _, _, _ = build_orchestrator(tmp_path, config=config, engine=again_engine)
    repeated = again.resume(result.job_id)

    assert repeated.status == JobStatus.COMPLETED
    assert repeated.synthesized_chunks == 0
    assert again_engine.attempted == []
    assert output_path.read_bytes() == original
    assert engine.attempted == calls_before
    job = store.load(result.job_id)
    assert job is not None
    assert job.status == JobStatus.COMPLETED


def test_changing_sentences_per_chunk_produces_a_new_job(tmp_path: Path) -> None:
    input_path = write_input(tmp_path, [paragraph("A", 4)])
    content_hash = sha256_file(input_path)
    config_a = make_config(sentences_per_chunk=5)
    config_b = make_config(sentences_per_chunk=2)
    assert compute_job_id(content_hash, config_a) != compute_job_id(content_hash, config_b)

    orchestrator_a, store, _, _, _ = build_orchestrator(tmp_path, config=config_a)
    result_a = orchestrator_a.convert(
        input_path, extractor=TxtExtractor(), output_path=tmp_path / "a.mp3"
    )
    orchestrator_b, _, _, _, _ = build_orchestrator(tmp_path, config=config_b)
    result_b = orchestrator_b.convert(
        input_path, extractor=TxtExtractor(), output_path=tmp_path / "b.mp3"
    )

    assert result_a.job_id != result_b.job_id
    assert store.load(result_a.job_id) is not None
    assert store.load(result_b.job_id) is not None
    assert len(list(store.jobs_dir.glob("*.json"))) == 2


def test_convert_resume_rejects_plan_config_mismatch(tmp_path: Path) -> None:
    input_path = write_input(tmp_path, [paragraph("A", 2)])
    output_path = tmp_path / "book.mp3"
    config = make_config(max_sentence_chars=2000)
    orchestrator, _, _, _, _ = build_orchestrator(tmp_path, config=config)
    result = orchestrator.convert(input_path, extractor=TxtExtractor(), output_path=output_path)

    changed = make_config(max_sentence_chars=100)
    other, _, _, _, _ = build_orchestrator(tmp_path, config=changed)
    with pytest.raises(JobConfigMismatchError, match="max_sentence_chars"):
        other.convert(
            input_path,
            extractor=TxtExtractor(),
            output_path=output_path,
            resume=True,
        )
    assert result.status == JobStatus.COMPLETED


def test_convert_resume_rejects_extractor_mismatch(tmp_path: Path) -> None:
    input_path = write_input(tmp_path, [paragraph("A", 2)])
    output_path = tmp_path / "book.mp3"
    orchestrator, _, _, _, _ = build_orchestrator(tmp_path)
    orchestrator.convert(input_path, extractor=TxtExtractor(), output_path=output_path)

    other, _, _, _, _ = build_orchestrator(tmp_path)
    with pytest.raises(JobConfigMismatchError, match="extractor"):
        other.convert(
            input_path,
            extractor=MarkdownExtractor(),
            output_path=output_path,
            resume=True,
        )


def test_convert_resume_rejects_output_path_mismatch(tmp_path: Path) -> None:
    input_path = write_input(tmp_path, [paragraph("A", 2)])
    orchestrator, _, _, _, _ = build_orchestrator(tmp_path)
    orchestrator.convert(
        input_path,
        extractor=TxtExtractor(),
        output_path=tmp_path / "book.mp3",
    )

    other, _, _, _, _ = build_orchestrator(tmp_path)
    with pytest.raises(JobConfigMismatchError, match="output"):
        other.convert(
            input_path,
            extractor=TxtExtractor(),
            output_path=tmp_path / "different.mp3",
            resume=True,
        )


def test_resume_rejects_changed_source(tmp_path: Path) -> None:
    input_path = write_input(tmp_path, [paragraph("A", 2)])
    output_path = tmp_path / "book.mp3"
    engine = FakeEngine(failures={1: 999})
    orchestrator, _store, _, _, _ = build_orchestrator(
        tmp_path,
        config=make_config(retries=0, sentences_per_chunk=1),
        engine=engine,
    )
    paused = orchestrator.convert(input_path, extractor=TxtExtractor(), output_path=output_path)
    assert paused.status == JobStatus.PAUSED

    input_path.write_text("Completely different content now.\n", encoding="utf-8")
    with pytest.raises(JobConfigMismatchError, match="changed"):
        orchestrator.resume(paused.job_id)


def test_convert_resume_tracks_a_moved_source_file(tmp_path: Path) -> None:
    input_path = write_input(tmp_path, [paragraph("A", 2)])
    output_path = tmp_path / "book.mp3"
    engine = FakeEngine(failures={1: 999})
    orchestrator, store, config, _, _ = build_orchestrator(
        tmp_path,
        config=make_config(retries=0, sentences_per_chunk=1),
        engine=engine,
    )
    paused = orchestrator.convert(input_path, extractor=TxtExtractor(), output_path=output_path)
    assert paused.status == JobStatus.PAUSED

    moved = tmp_path / "moved.txt"
    input_path.rename(moved)
    resumed_orchestrator, _, _, _, _ = build_orchestrator(
        tmp_path, config=config, engine=FakeEngine()
    )
    resumed = resumed_orchestrator.convert(
        moved,
        extractor=TxtExtractor(),
        output_path=output_path,
        resume=True,
    )
    assert resumed.status == JobStatus.COMPLETED
    job = store.load(paused.job_id)
    assert job is not None
    assert job.input_path == str(moved.resolve())


def test_stop_event_finishes_current_chunk_then_pauses(tmp_path: Path) -> None:
    input_path = write_input(tmp_path, [paragraph("A", 2), paragraph("B", 2)])
    output_path = tmp_path / "book.mp3"
    stop_event = threading.Event()
    engine = FakeEngine(on_success=lambda chunk: stop_event.set())
    orchestrator, store, config, _, _ = build_orchestrator(
        tmp_path,
        config=make_config(sentences_per_chunk=1, sleep_between_chunks=0.0),
        engine=engine,
    )

    result = orchestrator.convert(
        input_path,
        extractor=TxtExtractor(),
        output_path=output_path,
        stop_event=stop_event,
    )
    assert result.status == JobStatus.PAUSED
    assert result.completed_chunks == 1
    assert engine.attempted == [0]
    job = store.load(result.job_id)
    assert job is not None
    assert job.completed_chunk_indices == frozenset({0})
    assert store.work_dir(result.job_id).is_dir()

    stop_event.clear()
    resumed_orchestrator, _, _, _, _ = build_orchestrator(tmp_path, config=config)
    resumed = resumed_orchestrator.resume(result.job_id)
    assert resumed.status == JobStatus.COMPLETED


def test_max_runtime_pauses_cleanly(tmp_path: Path) -> None:
    input_path = write_input(tmp_path, [paragraph("A", 2), paragraph("B", 2)])
    output_path = tmp_path / "book.mp3"
    clock = FakeClock(step=10.0)
    orchestrator, store, _, engine, _ = build_orchestrator(
        tmp_path,
        config=make_config(max_runtime_seconds=15, sentences_per_chunk=1),
        clock=clock,
    )
    result = orchestrator.convert(input_path, extractor=TxtExtractor(), output_path=output_path)
    assert result.status == JobStatus.PAUSED
    assert result.completed_chunks == 1
    assert "Maximum runtime" in (result.message or "")
    assert len(engine.attempted) == 1
    job = store.load(result.job_id)
    assert job is not None
    assert job.status == JobStatus.PAUSED


def test_retry_with_exponential_backoff(tmp_path: Path) -> None:
    input_path = write_input(tmp_path, [paragraph("A", 2)])
    output_path = tmp_path / "book.mp3"
    sleeps: list[float] = []
    engine = FakeEngine(failures={0: 2})
    orchestrator, _, _, _, _ = build_orchestrator(
        tmp_path,
        config=make_config(retries=2, sentences_per_chunk=5),
        engine=engine,
        sleep=sleeps.append,
    )
    result = orchestrator.convert(input_path, extractor=TxtExtractor(), output_path=output_path)
    assert result.status == JobStatus.COMPLETED
    assert engine.attempted == [0, 0, 0]
    assert sleeps == [1.0, 2.0]


def test_retry_exhaustion_pauses(tmp_path: Path) -> None:
    input_path = write_input(tmp_path, [paragraph("A", 2)])
    output_path = tmp_path / "book.mp3"
    engine = FakeEngine(failures={0: 999})
    orchestrator, store, _, _, _ = build_orchestrator(
        tmp_path,
        config=make_config(retries=1),
        engine=engine,
    )
    result = orchestrator.convert(input_path, extractor=TxtExtractor(), output_path=output_path)
    assert result.status == JobStatus.PAUSED
    assert engine.attempted == [0, 0]
    assert "failed after 2 attempt" in (result.message or "")
    job = store.load(result.job_id)
    assert job is not None
    assert job.last_error and "simulated failure" in job.last_error


def test_merge_failure_pauses_and_resume_remerges_without_resynthesis(tmp_path: Path) -> None:
    input_path = write_input(tmp_path, [paragraph("A", 2)])
    output_path = tmp_path / "book.mp3"
    orchestrator, store, config, engine, _ = build_orchestrator(
        tmp_path, merger=FakeMerger(fail=True)
    )
    result = orchestrator.convert(input_path, extractor=TxtExtractor(), output_path=output_path)
    assert result.status == JobStatus.PAUSED
    assert result.completed_chunks == result.total_chunks
    assert engine.attempted == [0]
    job = store.load(result.job_id)
    assert job is not None
    assert job.completed_chunk_indices == frozenset({0})
    assert store.work_dir(result.job_id).is_dir()
    assert not output_path.exists()

    success_engine = FakeEngine()
    resumed_orchestrator, _, _, _, resumed_merger = build_orchestrator(
        tmp_path,
        config=config,
        engine=success_engine,
    )
    resumed = resumed_orchestrator.resume(result.job_id)
    assert resumed.status == JobStatus.COMPLETED
    assert success_engine.attempted == []
    assert resumed_merger.calls
    assert output_path.is_file()
    assert not store.work_dir(result.job_id).exists()


def test_deleted_output_is_regenerated_on_resume(tmp_path: Path) -> None:
    input_path = write_input(tmp_path, [paragraph("A", 2)])
    output_path = tmp_path / "book.mp3"
    orchestrator, store, config, _engine, _ = build_orchestrator(tmp_path)
    result = orchestrator.convert(input_path, extractor=TxtExtractor(), output_path=output_path)
    assert result.status == JobStatus.COMPLETED
    output_path.unlink()
    assert not store.work_dir(result.job_id).exists()

    new_engine = FakeEngine()
    resumed_orchestrator, _, _, _, _ = build_orchestrator(
        tmp_path, config=config, engine=new_engine
    )
    resumed = resumed_orchestrator.resume(result.job_id)
    assert resumed.status == JobStatus.COMPLETED
    assert new_engine.attempted == [0]
    assert output_path.is_file()


def test_section_boundaries_are_hard_boundaries(tmp_path: Path) -> None:
    document = SourceDocument(
        title="Sections",
        text="",
        page_count=0,
        sections=[
            Section(title="Chapter A", text="A1. A2.", level=1),
            Section(title="Chapter B", text="B1. B2.", level=1),
        ],
    )
    config = make_config(sentences_per_chunk=10)
    planner = DocumentPlanner(
        splitter=PysbdSentenceSplitter(),
        chunker=SentenceCountChunker(),
        config=config,
    )
    plan = planner.plan(document)
    assert [chunk.section_index for chunk in plan.chunks] == [0, 1]
    assert [chunk.section_title for chunk in plan.chunks] == ["Chapter A", "Chapter B"]
    assert plan.chunks[0].text == "A1. A2."
    assert plan.chunks[1].text == "B1. B2."


def test_progress_events(tmp_path: Path) -> None:
    input_path = write_input(tmp_path, [paragraph("A", 2), paragraph("B", 2)])
    output_path = tmp_path / "book.mp3"
    events: list[ProgressEvent] = []
    orchestrator, _, _, _, _ = build_orchestrator(
        tmp_path, config=make_config(sentences_per_chunk=2)
    )
    result = orchestrator.convert(
        input_path,
        extractor=TxtExtractor(),
        output_path=output_path,
        progress=events.append,
    )
    assert result.status == JobStatus.COMPLETED
    assert [event.chunk_index for event in events] == [0, 1]
    assert all(event.total_chunks == 2 for event in events)
    assert events[0].sentence_start == 0
    assert events[0].sentence_end == 1
    assert events[0].char_count > 0
    assert events[0].eta_seconds is not None
    assert events[-1].chunks_remaining == 0
    assert events[-1].eta_seconds is None


def test_empty_document_raises(tmp_path: Path) -> None:
    input_path = tmp_path / "empty.txt"
    input_path.write_text("", encoding="utf-8")
    orchestrator, store, _, engine, _ = build_orchestrator(tmp_path)
    with pytest.raises(EmptyDocumentError, match="No speakable text"):
        orchestrator.convert(input_path, extractor=TxtExtractor(), output_path=tmp_path / "out.mp3")
    assert engine.attempted == []
    assert list(store.jobs_dir.glob("*.json")) == []


def engine_calls_to_chunks(store: JsonJobStore, job_id: str) -> tuple[Chunk, ...]:
    job = store.load(job_id)
    assert job is not None
    return job.chunks
