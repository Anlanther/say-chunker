"""Typer CLI entrypoint.

This is the composition root: it builds concrete adapters and injects them into
the :class:`Orchestrator`.
"""

from __future__ import annotations

import hashlib
import shutil
import signal
import sys
from pathlib import Path
from types import FrameType
from typing import Annotated, Any, Optional

import typer

from say_chunker.adapters.extractors import format_for_path, get_extractor
from say_chunker.adapters.ffmpeg_merger import FFmpegMerger
from say_chunker.adapters.json_job_store import JSONJobStore
from say_chunker.adapters.pysbd_splitter import PySBDSentenceSplitter
from say_chunker.adapters.say_engine import SayEngine, list_voices, validate_voice
from say_chunker.config import Settings, load_settings, parse_duration
from say_chunker.domain.errors import MissingDependencyError, TTSAppError
from say_chunker.domain.models import JobStatus
from say_chunker.pipeline.chunker import SentenceChunker
from say_chunker.pipeline.orchestrator import (
    Orchestrator,
    ProgressEvent,
    content_hash_of_bytes,
    make_job_id,
)
from say_chunker.pipeline.throttle import Throttle
from say_chunker.utils.logging import setup_logging

app = typer.Typer(name="tts", no_args_is_help=True, help="Local text-to-MP3 audiobook converter.")


# -- helpers ---------------------------------------------------------------


def _fail(exc: TTSAppError) -> None:
    typer.echo(f"error: {exc}", err=True)
    raise typer.Exit(1)


def _state_dir(settings: Settings) -> Path:
    return Path(settings.state_dir).expanduser()


def _check_dependencies(settings: Settings) -> None:
    say = Path(settings.say_binary).expanduser()
    if not say.exists():
        raise MissingDependencyError(f"`say` not found at {say}; macOS `say` is required")
    if shutil.which(settings.ffmpeg_binary) is None:
        raise MissingDependencyError(
            "`ffmpeg` not found on PATH; install with `brew install ffmpeg`"
        )
    if shutil.which(settings.ffprobe_binary) is None:
        raise MissingDependencyError(
            "`ffprobe` not found on PATH; install with `brew install ffmpeg`"
        )


def _resolve_input(input_path: Path, settings: Settings) -> Path:
    if str(input_path) != "-":
        path = input_path.expanduser().resolve()
        if not path.exists():
            raise TTSAppError(f"input file not found: {path}")
        return path

    data = sys.stdin.buffer.read()
    digest = hashlib.sha256(data).hexdigest()[:16]
    target = _state_dir(settings) / "tmp" / "stdin" / f"stdin_{digest}.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def _build_orchestrator(settings: Settings, on_progress: Any | None) -> Orchestrator:
    state = _state_dir(settings)
    return Orchestrator(
        splitter=PySBDSentenceSplitter(),
        chunker=SentenceChunker(),
        engine=SayEngine(Path(settings.say_binary).expanduser()),
        merger=FFmpegMerger(settings.ffmpeg_binary),
        store=JSONJobStore(state / "jobs", temp_dir=state / "tmp"),
        throttle=Throttle(settings.sleep_between_chunks),
        temp_dir=state / "tmp",
        on_progress=on_progress,
    )


def _install_sigint(orchestrator: Orchestrator) -> None:
    def handler(signum: int, frame: FrameType | None) -> None:
        del signum, frame
        orchestrator.request_stop()

    signal.signal(signal.SIGINT, handler)


def _print_progress(event: ProgressEvent) -> None:
    eta = f"eta {event.eta_seconds:.0f}s" if event.eta_seconds is not None else "eta -"
    typer.echo(
        f"chunk {event.chunk_index}/{event.total_chunks} [{event.status}] "
        f"sentences {event.sentence_start}-{event.sentence_end} "
        f"chars={event.chars} elapsed={event.elapsed_seconds:.1f}s {eta}"
    )


def _get_extractor(fmt: str) -> Any:
    try:
        return get_extractor(fmt)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _report_finished(job: Any) -> None:
    if job.status == JobStatus.COMPLETED:
        typer.echo(f"wrote {job.output_path}")
    else:
        typer.echo(f"paused (status={job.status.value}); resume with: tts resume {job.job_id}")


# -- commands --------------------------------------------------------------


@app.command()
def convert(
    input_path: Annotated[Path, typer.Argument(help="Input .txt/.md/.pdf file, or '-' for stdin")],
    output: Annotated[
        Optional[str], typer.Option("-o", "--output", help="Output MP3 path (default: <title>.mp3)")
    ] = None,
    voice: Annotated[
        Optional[str], typer.Option("--voice", help="say voice (default: Samantha)")
    ] = None,
    rate: Annotated[
        Optional[int], typer.Option("--rate", help="Words per minute (default: 200)")
    ] = None,
    sentences_per_chunk: Annotated[
        Optional[int],
        typer.Option("--sentences-per-chunk", help="Sentences per say call (default: 5)"),
    ] = None,
    max_sentence_chars: Annotated[
        Optional[int],
        typer.Option("--max-sentence-chars", help="Long-sentence split threshold (default: 2000)"),
    ] = None,
    language: Annotated[
        Optional[str], typer.Option("--language", help="pysbd language code (default: en)")
    ] = None,
    sleep: Annotated[
        Optional[float],
        typer.Option(
            "--sleep",
            "--sleep-between-chunks",
            help="Cooldown seconds between chunks (default: 0.5)",
        ),
    ] = None,
    retries: Annotated[
        Optional[int], typer.Option("--retries", help="Retries per failed chunk (default: 2)")
    ] = None,
    resume: Annotated[
        bool, typer.Option("--resume", help="Resume an existing job if present")
    ] = False,
    no_normalize: Annotated[
        bool, typer.Option("--no-normalize", help="Disable loudnorm normalization")
    ] = False,
    pdf_password: Annotated[
        Optional[str], typer.Option("--pdf-password", help="Password for encrypted PDFs")
    ] = None,
    fmt: Annotated[
        Optional[str], typer.Option("--format", help="Override format: txt, md, or pdf")
    ] = None,
    max_runtime: Annotated[
        Optional[str], typer.Option("--max-runtime", help="Cap total runtime, e.g. 2h")
    ] = None,
) -> None:
    overrides: dict[str, Any] = {
        "voice": voice,
        "rate": rate,
        "sentences_per_chunk": sentences_per_chunk,
        "max_sentence_chars": max_sentence_chars,
        "language": language,
        "sleep_between_chunks": sleep,
        "retries": retries,
        "max_runtime": max_runtime,
        "pdf_password": pdf_password,
        "format": fmt,
    }
    if no_normalize:
        overrides["normalize"] = False

    settings = load_settings(overrides)

    try:
        _check_dependencies(settings)
        validate_voice(settings.voice, Path(settings.say_binary).expanduser())

        is_stdin = str(input_path) == "-"
        resolved_input = _resolve_input(input_path, settings)
        content_hash = content_hash_of_bytes(resolved_input.read_bytes())

        actual_format = settings.format
        if actual_format is None:
            if is_stdin:
                raise typer.BadParameter("stdin input requires --format")
            actual_format = format_for_path(resolved_input)
        extractor = _get_extractor(actual_format)

        job_id = make_job_id(
            content_hash,
            settings.voice,
            settings.rate,
            settings.sentences_per_chunk,
            settings.language,
        )
        setup_logging(_state_dir(settings) / "jobs" / f"{job_id}.log")

        orchestrator = _build_orchestrator(settings, _print_progress)
        _install_sigint(orchestrator)

        job = orchestrator.run(
            extractor=extractor,
            input_path=resolved_input,
            output_path=Path(output) if output is not None else None,
            config=settings,
            content_hash=content_hash,
            resume=resume,
        )
    except TTSAppError as exc:
        _fail(exc)

    _report_finished(job)


@app.command()
def resume(
    job_id: Annotated[str, typer.Argument(help="Job id to resume")],
    output: Annotated[
        Optional[str], typer.Option("-o", "--output", help="Override output MP3 path")
    ] = None,
) -> None:
    settings = load_settings()

    try:
        _check_dependencies(settings)
        state = _state_dir(settings)
        store = JSONJobStore(state / "jobs", temp_dir=state / "tmp")
        job = store.load(job_id)
        if job is None:
            raise typer.BadParameter(f"no job found with id {job_id!r}")

        stored = Settings(**job.config)
        validate_voice(stored.voice, Path(settings.say_binary).expanduser())
        setup_logging(state / "jobs" / f"{job_id}.log")

        orchestrator = _build_orchestrator(settings, _print_progress)
        _install_sigint(orchestrator)
        job = orchestrator.resume(
            job_id=job_id, extractor=get_extractor(job.extractor), config=settings
        )
    except TTSAppError as exc:
        _fail(exc)

    _report_finished(job)


@app.command("list-jobs")
def list_jobs() -> None:
    settings = load_settings()
    store = JSONJobStore(_state_dir(settings) / "jobs")
    jobs = store.list_jobs()
    if not jobs:
        typer.echo("no jobs")
        return
    for job in jobs:
        typer.echo(
            f"{job.job_id}\t{job.status.value}\t"
            f"{len(job.completed_chunks)}/{len(job.chunks)}\t{job.title}"
        )


@app.command()
def clean(
    older_than: Annotated[Optional[str], typer.Option("--older-than", help="e.g. 7d")] = None,
) -> None:
    settings = load_settings()
    state = _state_dir(settings)
    days = parse_duration(older_than) / 86400.0 if older_than else None
    store = JSONJobStore(state / "jobs", temp_dir=state / "tmp")
    removed = store.clean(older_than_days=days)
    typer.echo(f"removed {removed} job(s)")


@app.command()
def inspect(
    input_path: Annotated[Path, typer.Argument(help="Input .txt/.md/.pdf file, or '-' for stdin")],
    sentences_per_chunk: Annotated[
        Optional[int],
        typer.Option("--sentences-per-chunk", help="Sentences per chunk (default: 5)"),
    ] = None,
    max_sentence_chars: Annotated[
        Optional[int],
        typer.Option("--max-sentence-chars", help="Long-sentence threshold (default: 2000)"),
    ] = None,
    language: Annotated[
        Optional[str], typer.Option("--language", help="pysbd language code (default: en)")
    ] = None,
    fmt: Annotated[
        Optional[str], typer.Option("--format", help="Override format: txt, md, or pdf")
    ] = None,
    pdf_password: Annotated[
        Optional[str], typer.Option("--pdf-password", help="Password for encrypted PDFs")
    ] = None,
) -> None:
    settings = load_settings(
        {
            "sentences_per_chunk": sentences_per_chunk,
            "max_sentence_chars": max_sentence_chars,
            "language": language,
            "format": fmt,
            "pdf_password": pdf_password,
        }
    )

    is_stdin = str(input_path) == "-"
    try:
        resolved = _resolve_input(input_path, settings)
    except TTSAppError as exc:
        _fail(exc)

    actual_format = settings.format
    if actual_format is None:
        if is_stdin:
            raise typer.BadParameter("stdin input requires --format")
        actual_format = format_for_path(resolved)
    extractor = _get_extractor(actual_format)

    try:
        doc = extractor.extract(resolved, password=settings.pdf_password)
    except TTSAppError as exc:
        _fail(exc)

    splitter = PySBDSentenceSplitter()
    chunker = SentenceChunker()

    total_sentences = 0
    chunks: list[Any] = []
    first_index = 0
    for section_index, section in enumerate(doc.sections):
        sentences = splitter.split(section.text, language=settings.language)
        total_sentences += len(sentences)
        section_chunks = chunker.chunk(
            sentences,
            section_index=section_index,
            sentences_per_chunk=settings.sentences_per_chunk,
            max_sentence_chars=settings.max_sentence_chars,
            first_index=first_index,
        )
        chunks.extend(section_chunks)
        first_index += len(section_chunks)

    typer.echo(f"extractor: {extractor.name}")
    typer.echo(f"title: {doc.title}")
    typer.echo(f"pages: {doc.page_count}")
    typer.echo(f"sections: {len(doc.sections)}")
    typer.echo(f"sentences: {total_sentences}")
    typer.echo(f"chunks: {len(chunks)}")
    for warning in doc.warnings:
        typer.echo(f"warning: {warning}", err=True)


@app.command()
def voices() -> None:
    settings = load_settings()
    say = Path(settings.say_binary).expanduser()
    if not say.exists():
        raise typer.BadParameter(f"`say` not found at {say}")
    try:
        for voice in sorted(list_voices(say)):
            typer.echo(voice)
    except TTSAppError as exc:
        _fail(exc)


if __name__ == "__main__":
    app()
