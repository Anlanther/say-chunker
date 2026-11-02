"""Typer CLI and composition root.

This is the only layer that constructs concrete adapters. The orchestrator
receives ports and never imports adapters itself.
"""

from __future__ import annotations

import shutil
import signal
import sys
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import NoReturn

import typer
from pydantic import ValidationError

from say_chunker.adapters.extractors import (
    SUPPORTED_FORMATS,
    format_for_path,
    get_extractor,
)
from say_chunker.adapters.ffmpeg_merger import FFmpegMerger
from say_chunker.adapters.json_job_store import JsonJobStore
from say_chunker.adapters.pysbd_splitter import PysbdSentenceSplitter
from say_chunker.adapters.say_engine import SayEngine
from say_chunker.config import AppSettings
from say_chunker.domain.errors import (
    ConfigurationError,
    JobNotFoundError,
    PDFPasswordRequiredError,
    TTSAppError,
)
from say_chunker.domain.models import (
    ConversionResult,
    JobStatus,
    ProgressEvent,
    SourceDocument,
)
from say_chunker.domain.ports import TextExtractor
from say_chunker.pipeline.chunker import SentenceCountChunker
from say_chunker.pipeline.orchestrator import (
    DocumentPlanner,
    Orchestrator,
    compute_job_id,
)
from say_chunker.utils.logging import configure_logging
from say_chunker.utils.textutil import (
    ensure_directory_writable,
    ensure_writable_output,
    format_duration,
    parse_duration,
    sha256_file,
)

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help=(
        "Convert long .txt, .md, and .pdf documents into a single MP3 using the "
        "built-in macOS `say` engine. Fully offline."
    ),
)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command("convert")
def convert(
    input_source: str = typer.Argument(..., metavar="INPUT", help="Input file, or - for stdin."),
    output: Path | None = typer.Option(
        None, "-o", "--output", help="Output MP3 path (default: <title>.mp3)."
    ),
    voice: str | None = typer.Option(None, "--voice", help="say voice name (default: Samantha)."),
    rate: int | None = typer.Option(
        None, "--rate", help="Speaking rate in words per minute (default: 200)."
    ),
    sentences_per_chunk: int | None = typer.Option(
        None, "--sentences-per-chunk", help="Maximum sentences per say invocation (default: 5)."
    ),
    max_sentence_chars: int | None = typer.Option(
        None,
        "--max-sentence-chars",
        help="Split a single sentence above this length (default: 2000).",
    ),
    language: str | None = typer.Option(
        None, "--language", help="pysbd language code (default: en)."
    ),
    sleep: float | None = typer.Option(
        None,
        "--sleep-between-chunks",
        "--sleep",
        help="Cooldown in seconds between say invocations (default: 0.5).",
    ),
    retries: int | None = typer.Option(
        None, "--retries", help="Retries per failed chunk (default: 2)."
    ),
    normalize: bool | None = typer.Option(
        None, "--normalize/--no-normalize", help="Apply ffmpeg loudnorm (default: on)."
    ),
    bitrate: int | None = typer.Option(
        None, "--bitrate-kbps", help="MP3 bitrate in kbps, 128-192 (default: 128)."
    ),
    timeout: float | None = typer.Option(
        None, "--timeout-seconds", help="Per-chunk say subprocess timeout (default: 300)."
    ),
    resume: bool = typer.Option(
        False, "--resume", help="Resume a matching paused job instead of starting over."
    ),
    pdf_password: str | None = typer.Option(
        None, "--pdf-password", help="Password for an encrypted PDF."
    ),
    input_format: str | None = typer.Option(
        None, "--format", help="Override input format: txt, md, or pdf."
    ),
    max_runtime: str | None = typer.Option(
        None, "--max-runtime", help="Cap total runtime, e.g. 2h, 90m, or 3600s."
    ),
) -> None:
    """Convert a document into one MP3."""
    try:
        settings = _make_settings(
            voice=voice,
            rate=rate,
            sentences_per_chunk=sentences_per_chunk,
            max_sentence_chars=max_sentence_chars,
            language=language,
            sleep_between_chunks=sleep,
            retries=retries,
            normalize=normalize,
            bitrate_kbps=bitrate,
            timeout_seconds=timeout,
            max_runtime_seconds=_parse_optional_duration(max_runtime, "--max-runtime"),
        )
        result = _convert_impl(
            input_source=input_source,
            input_format=input_format,
            output=output,
            pdf_password=pdf_password,
            resume=resume,
            settings=settings,
        )
        _finish(result)
    except (TTSAppError, ValidationError, OSError) as exc:
        _handle_error(exc)
    except (KeyboardInterrupt, typer.Abort) as exc:
        typer.echo("\nInterrupted.", err=True)
        raise typer.Exit(130) from exc


@app.command("resume")
def resume(
    job_id: str = typer.Argument(..., metavar="JOB_ID", help="Job id from tts list-jobs."),
) -> None:
    """Resume a paused job using its stored configuration."""
    try:
        settings = _make_settings()
        result = _resume_impl(job_id, settings)
        _finish(result)
    except (TTSAppError, ValidationError, OSError) as exc:
        _handle_error(exc)
    except (KeyboardInterrupt, typer.Abort) as exc:
        typer.echo("\nInterrupted.", err=True)
        raise typer.Exit(130) from exc


@app.command("list-jobs")
def list_jobs() -> None:
    """List persisted jobs and their progress."""
    try:
        settings = _make_settings()
        _list_jobs_impl(settings)
    except (TTSAppError, ValidationError, OSError) as exc:
        _handle_error(exc)


@app.command("clean")
def clean(
    older_than: str = typer.Option(
        "7d", "--older-than", help="Remove jobs older than this duration (e.g. 7d, 24h)."
    ),
) -> None:
    """Remove old job state, logs, and temporary audio."""
    try:
        settings = _make_settings()
        _clean_impl(older_than, settings)
    except (TTSAppError, ValidationError, OSError) as exc:
        _handle_error(exc)


@app.command("inspect")
def inspect(
    input_source: str = typer.Argument(..., metavar="INPUT", help="Input file, or - for stdin."),
    input_format: str | None = typer.Option(
        None, "--format", help="Override input format: txt, md, or pdf."
    ),
    pdf_password: str | None = typer.Option(
        None, "--pdf-password", help="Password for an encrypted PDF."
    ),
    sentences_per_chunk: int | None = typer.Option(
        None, "--sentences-per-chunk", help="Plan chunks at this sentence count (default: 5)."
    ),
    max_sentence_chars: int | None = typer.Option(
        None, "--max-sentence-chars", help="Long-sentence safety threshold (default: 2000)."
    ),
    language: str | None = typer.Option(
        None, "--language", help="pysbd language code (default: en)."
    ),
) -> None:
    """Dry-run: report extractor, sections, sentences, chunks, and warnings."""
    try:
        settings = _make_settings(
            sentences_per_chunk=sentences_per_chunk,
            max_sentence_chars=max_sentence_chars,
            language=language,
        )
        _inspect_impl(input_source, input_format, pdf_password, settings)
    except (TTSAppError, ValidationError, OSError) as exc:
        _handle_error(exc)
    except (KeyboardInterrupt, typer.Abort) as exc:
        typer.echo("\nInterrupted.", err=True)
        raise typer.Exit(130) from exc


@app.command("voices")
def voices() -> None:
    """List voices installed for the macOS say command."""
    try:
        settings = _make_settings()
        configure_logging(None)
        engine = SayEngine(say_path=settings.say_binary)
        available = engine.list_voices()
    except (TTSAppError, ValidationError, OSError) as exc:
        _handle_error(exc)
        return
    if not available:
        typer.echo("No voices were reported by `say -v '?'`.")
        return
    typer.echo(f"Available voices ({len(available)}):")
    for name in available:
        typer.echo(f"  {name}")


# ---------------------------------------------------------------------------
# Input preparation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _PreparedInput:
    path: Path
    format_name: str
    display: str
    temporary: Path | None

    @property
    def persist_path(self) -> bool:
        return self.temporary is None

    def cleanup(self) -> None:
        if self.temporary is None:
            return
        if self.temporary.is_dir():
            shutil.rmtree(self.temporary, ignore_errors=True)
        else:
            self.temporary.unlink(missing_ok=True)


def _prepare_input(input_source: str, input_format: str | None) -> _PreparedInput:
    if input_source == "-":
        if input_format is None:
            raise ConfigurationError("Reading from stdin requires --format {txt,md,pdf}.")
        format_name = _normalize_format(input_format)
        buffer = getattr(sys.stdin, "buffer", None)
        try:
            data = bytes(buffer.read()) if buffer is not None else sys.stdin.read().encode("utf-8")
        except OSError as exc:
            raise ConfigurationError(f"Could not read stdin: {exc}") from exc
        if not data:
            raise ConfigurationError("stdin was empty.")
        temporary_dir = Path(tempfile.mkdtemp(prefix="tts-stdin-"))
        temporary = temporary_dir / f"stdin.{format_name}"
        try:
            temporary.write_bytes(data)
        except OSError as exc:
            shutil.rmtree(temporary_dir, ignore_errors=True)
            raise ConfigurationError(f"Could not buffer stdin: {exc}") from exc
        return _PreparedInput(
            path=temporary,
            format_name=format_name,
            display="<stdin>",
            temporary=temporary_dir,
        )

    path = Path(input_source).expanduser()
    if not path.is_file():
        raise ConfigurationError(f"Input file not found: {path}")
    format_name = (
        _normalize_format(input_format) if input_format is not None else format_for_path(path)
    )
    return _PreparedInput(
        path=path,
        format_name=format_name,
        display=str(path),
        temporary=None,
    )


def _normalize_format(value: str) -> str:
    key = value.strip().lower().lstrip(".")
    if key not in SUPPORTED_FORMATS:
        raise ConfigurationError(
            f"Unsupported format {value!r}. Choose one of: {', '.join(SUPPORTED_FORMATS)}."
        )
    return key


# ---------------------------------------------------------------------------
# Implementations
# ---------------------------------------------------------------------------


def _convert_impl(
    *,
    input_source: str,
    input_format: str | None,
    output: Path | None,
    pdf_password: str | None,
    resume: bool,
    settings: AppSettings,
) -> ConversionResult:
    prepared = _prepare_input(input_source, input_format)
    try:
        if output is not None:
            ensure_writable_output(output.resolve())
        config = settings.to_synthesis_config()
        try:
            content_hash = sha256_file(prepared.path)
        except OSError as exc:
            raise ConfigurationError(f"Could not read input {prepared.display}: {exc}") from exc
        job_id = compute_job_id(content_hash, config)
        store = JsonJobStore(settings.state_directory)
        log_path = store.log_path(job_id)
        configure_logging(log_path)

        existing = store.load(job_id) if resume else None
        if resume:
            if existing is None:
                typer.echo("No resumable job found; starting a fresh conversion.")
            else:
                typer.echo(
                    f"Resuming job {job_id}: "
                    f"{existing.completed_count}/{existing.chunk_count} chunks complete."
                )
        else:
            typer.echo(f"Job {job_id} (log: {log_path})")

        if existing is None:
            # A fresh job's default output lives in the current directory.
            # Probe it now if the user did not supply an explicit -o path.
            ensure_directory_writable(Path.cwd())

        engine = SayEngine(say_path=settings.say_binary)
        engine.validate_voice(config.voice)
        merger = FFmpegMerger(
            ffmpeg_path=settings.ffmpeg_binary,
            ffprobe_path=settings.ffprobe_binary,
        )
        merger.validate()

        planner = DocumentPlanner(
            splitter=PysbdSentenceSplitter(),
            chunker=SentenceCountChunker(),
            config=config,
        )
        orchestrator = Orchestrator(
            planner=planner,
            engine=engine,
            merger=merger,
            job_store=store,
            config=config,
        )

        stop_event = threading.Event()
        password = pdf_password
        with _graceful_signals(stop_event):
            while True:
                extractor = get_extractor(prepared.format_name, password=password)
                try:
                    return orchestrator.convert(
                        prepared.path,
                        extractor=extractor,
                        output_path=output,
                        resume=resume,
                        persist_input_path=prepared.persist_path,
                        stop_event=stop_event,
                        progress=_progress_printer,
                    )
                except PDFPasswordRequiredError:
                    if password is not None:
                        raise
                    password = _prompt_pdf_password()
    finally:
        prepared.cleanup()


def _resume_impl(job_id: str, settings: AppSettings) -> ConversionResult:
    store = JsonJobStore(settings.state_directory)
    job = store.load(job_id)
    if job is None:
        raise JobNotFoundError(job_id)

    log_path = store.log_path(job_id)
    configure_logging(log_path)

    if job.status == JobStatus.COMPLETED and Path(job.output_path).is_file():
        return ConversionResult(
            job_id=job_id,
            status=JobStatus.COMPLETED,
            output_path=Path(job.output_path),
            total_chunks=job.chunk_count,
            completed_chunks=job.chunk_count,
            synthesized_chunks=0,
            elapsed_seconds=0.0,
            message=f"Job already complete: {job.output_path}",
        )

    engine = SayEngine(say_path=settings.say_binary)
    engine.validate_voice(job.config.voice)
    merger = FFmpegMerger(
        ffmpeg_path=settings.ffmpeg_binary,
        ffprobe_path=settings.ffprobe_binary,
    )
    merger.validate()

    planner = DocumentPlanner(
        splitter=PysbdSentenceSplitter(),
        chunker=SentenceCountChunker(),
        config=job.config,
    )
    orchestrator = Orchestrator(
        planner=planner,
        engine=engine,
        merger=merger,
        job_store=store,
        config=job.config,
    )

    typer.echo(
        f"Resuming job {job_id}: {job.completed_count}/{job.chunk_count} chunks complete "
        f"(log: {log_path})"
    )
    stop_event = threading.Event()
    with _graceful_signals(stop_event):
        return orchestrator.resume(
            job_id,
            stop_event=stop_event,
            progress=_progress_printer,
        )


def _inspect_impl(
    input_source: str,
    input_format: str | None,
    pdf_password: str | None,
    settings: AppSettings,
) -> None:
    prepared = _prepare_input(input_source, input_format)
    try:
        configure_logging(None)
        config = settings.to_synthesis_config()
        document, extractor = _extract_with_password(prepared, pdf_password=pdf_password)
        planner = DocumentPlanner(
            splitter=PysbdSentenceSplitter(),
            chunker=SentenceCountChunker(),
            config=config,
        )
        plan = planner.plan(document, config=config)

        typer.echo(f"File:        {prepared.display}")
        typer.echo(f"Extractor:   {extractor.name}")
        typer.echo(f"Title:       {document.title}")
        if document.page_count:
            typer.echo(f"Pages:       {document.page_count}")
        typer.echo(f"Sections:    {plan.section_count}")
        if plan.speakable_sentence_count != plan.sentence_count:
            typer.echo(
                f"Sentences:   {plan.sentence_count} "
                f"({plan.speakable_sentence_count} after long-sentence splits)"
            )
        else:
            typer.echo(f"Sentences:   {plan.sentence_count}")
        typer.echo(
            f"Chunks:      {len(plan.chunks)} (at {config.sentences_per_chunk} sentences/chunk)"
        )
        typer.echo(f"Language:    {config.language}")
        typer.echo(f"Max chars:   {config.max_sentence_chars} per sentence")
        if plan.warnings:
            typer.echo("Warnings:")
            for warning in plan.warnings:
                typer.echo(f"  - {warning}")
    finally:
        prepared.cleanup()


def _extract_with_password(
    prepared: _PreparedInput,
    *,
    pdf_password: str | None,
) -> tuple[SourceDocument, TextExtractor]:
    password = pdf_password
    while True:
        extractor = get_extractor(prepared.format_name, password=password)
        try:
            return extractor.extract(prepared.path), extractor
        except PDFPasswordRequiredError:
            if password is not None:
                raise
            password = _prompt_pdf_password()


def _list_jobs_impl(settings: AppSettings) -> None:
    configure_logging(None)
    store = JsonJobStore(settings.state_directory)
    jobs = store.list_jobs()
    if not jobs:
        typer.echo(f"No jobs found in {store.jobs_dir}")
        return
    typer.echo(f"{'JOB ID':<16}  {'STATUS':<10}  {'CHUNKS':<9}  {'UPDATED':<25}  OUTPUT")
    for job in jobs:
        chunks = f"{job.completed_count}/{job.chunk_count}"
        typer.echo(
            f"{job.job_id:<16}  {job.status.value:<10}  {chunks:<9}  "
            f"{job.updated_at:<25}  {job.output_path}"
        )


def _clean_impl(older_than: str, settings: AppSettings) -> None:
    configure_logging(None)
    try:
        seconds = parse_duration(older_than)
    except ValueError as exc:
        raise ConfigurationError(f"Invalid --older-than: {exc}") from exc
    store = JsonJobStore(settings.state_directory)
    deleted = store.clean_older_than(seconds)
    if not deleted:
        typer.echo("Nothing to clean.")
        return
    typer.echo(f"Removed {len(deleted)} job(s):")
    for job_id in deleted:
        typer.echo(f"  {job_id}")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_settings(**overrides: object) -> AppSettings:
    cleaned = {key: value for key, value in overrides.items() if value is not None}
    try:
        return AppSettings(**cleaned)  # type: ignore[arg-type]
    except ValidationError as exc:
        raise ConfigurationError(_format_validation_error(exc)) from exc
    except Exception as exc:
        raise ConfigurationError(f"Could not load configuration: {exc}") from exc


def _format_validation_error(exc: ValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ()))
        message = str(error.get("msg", "invalid value"))
        parts.append(f"{location}: {message}" if location else message)
    return "; ".join(parts) if parts else str(exc)


def _parse_optional_duration(value: str | None, option: str) -> float | None:
    if value is None:
        return None
    try:
        return parse_duration(value)
    except ValueError as exc:
        raise ConfigurationError(f"Invalid {option}: {exc}") from exc


def _prompt_pdf_password() -> str:
    try:
        return str(typer.prompt("PDF password", hide_input=True))
    except (typer.Abort, EOFError) as exc:
        raise ConfigurationError("PDF password entry was cancelled.") from exc


def _progress_printer(event: ProgressEvent) -> None:
    eta = "n/a" if event.eta_seconds is None else format_duration(event.eta_seconds)
    typer.echo(
        f"[{event.chunk_index + 1}/{event.total_chunks}] "
        f"chunk {event.chunk_index}: sentences {event.sentence_start}-{event.sentence_end}, "
        f"{event.char_count} chars, elapsed {format_duration(event.elapsed_seconds)}, ETA {eta}"
    )


def _finish(result: ConversionResult) -> None:
    if result.status == JobStatus.COMPLETED:
        if result.synthesized_chunks == 0:
            typer.echo(result.message or "Job already complete.")
        else:
            typer.echo(
                f"Done: {result.output_path} "
                f"({result.total_chunks} chunk(s), {format_duration(result.elapsed_seconds)})"
            )
        return

    typer.echo(f"Paused: {result.message or 'conversion paused'}")
    typer.echo(f"Completed {result.completed_chunks}/{result.total_chunks} chunk(s).")
    if not result.message:
        typer.echo(f"Resume with: tts resume {result.job_id}")
    raise typer.Exit(1)


def _handle_error(exc: BaseException) -> NoReturn:
    message = _format_validation_error(exc) if isinstance(exc, ValidationError) else str(exc)
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(1) from exc


@contextmanager
def _graceful_signals(stop_event: threading.Event) -> Iterator[None]:
    previous_int = signal.getsignal(signal.SIGINT)
    previous_term = signal.getsignal(signal.SIGTERM)

    def handler(signum: int, frame: FrameType | None) -> None:
        del signum, frame
        if stop_event.is_set():
            typer.echo(
                "\nAlready finishing the current chunk; please wait...",
                err=True,
            )
            return
        stop_event.set()
        typer.echo(
            "\nInterrupt received; finishing the current chunk and saving state...",
            err=True,
        )

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)


if __name__ == "__main__":
    app()
