from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from say_chunker.adapters.json_job_store import JsonJobStore
from say_chunker.cli import app
from tests.fake_bins import write_fake_ffmpeg, write_fake_ffprobe, write_fake_say

FIXTURES = Path(__file__).parent / "fixtures"
runner = CliRunner()


@pytest.fixture
def cli_env(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    env = {
        "TTS_CONFIG_FILE": str(tmp_path / "missing.toml"),
        "TTS_STATE_DIR": str(tmp_path / "state"),
        "TTS_SAY_BINARY": str(write_fake_say(bin_dir)),
        "TTS_FFMPEG_BINARY": str(write_fake_ffmpeg(bin_dir)),
        "TTS_FFPROBE_BINARY": str(write_fake_ffprobe(bin_dir)),
        "FAKE_FFMPEG_LOG": str(tmp_path / "ffmpeg.log"),
    }
    return env


def invoke(args: list[str], env: dict[str, str]) -> Result:
    return runner.invoke(app, args, env=env)


def write_input(tmp_path: Path) -> Path:
    path = tmp_path / "input.txt"
    path.write_text(
        "First sentence. Second sentence. Third sentence. Fourth sentence.\n\n"
        "A new paragraph. With two sentences.\n",
        encoding="utf-8",
    )
    return path


def test_inspect_pdf_reports_structure(cli_env: dict[str, str]) -> None:
    result = invoke(["inspect", str(FIXTURES / "sample.pdf")], cli_env)
    assert result.exit_code == 0, result.output
    assert "Extractor:   pdf" in result.output
    assert "Title:       The Great Test Book" in result.output
    assert "Sections:    2" in result.output
    assert "Sentences:" in result.output
    assert "Chunks:" in result.output
    assert "Language:    en" in result.output
    assert not (Path(cli_env["TTS_STATE_DIR"]) / "jobs").exists()


def test_inspect_respects_sentences_per_chunk(cli_env: dict[str, str]) -> None:
    default = invoke(["inspect", str(FIXTURES / "sample.pdf")], cli_env)
    smaller = invoke(
        ["inspect", str(FIXTURES / "sample.pdf"), "--sentences-per-chunk", "1"],
        cli_env,
    )
    assert default.exit_code == 0
    assert smaller.exit_code == 0
    default_chunks = _chunk_count(default.output)
    smaller_chunks = _chunk_count(smaller.output)
    assert smaller_chunks > default_chunks


def test_inspect_rejects_unknown_extension(cli_env: dict[str, str], tmp_path: Path) -> None:
    path = tmp_path / "book.docx"
    path.write_text("hello", encoding="utf-8")
    result = invoke(["inspect", str(path)], cli_env)
    assert result.exit_code == 1
    assert "Unsupported file type" in result.output
    assert "--format" in result.output


def test_voices_lists_fake_voice(cli_env: dict[str, str]) -> None:
    result = invoke(["voices"], cli_env)
    assert result.exit_code == 0
    assert "Samantha" in result.output


def test_convert_end_to_end_and_list_jobs(cli_env: dict[str, str], tmp_path: Path) -> None:
    input_path = write_input(tmp_path)
    output_path = tmp_path / "out.mp3"
    result = invoke(
        [
            "convert",
            str(input_path),
            "-o",
            str(output_path),
            "--sentences-per-chunk",
            "2",
            "--sleep",
            "0",
        ],
        cli_env,
    )
    assert result.exit_code == 0, result.output
    assert "Done:" in result.output
    assert "[1/" in result.output
    assert output_path.read_text(encoding="utf-8") == "fake audio"

    store = JsonJobStore(Path(cli_env["TTS_STATE_DIR"]))
    jobs = store.list_jobs()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.status.value == "completed"
    assert job.completed_count == job.chunk_count == 3
    assert Path(job.output_path) == output_path
    assert job.config.voice == "Samantha"

    listed = invoke(["list-jobs"], cli_env)
    assert listed.exit_code == 0
    assert job.job_id in listed.output
    assert "completed" in listed.output


def test_convert_passes_normalize_and_bitrate_to_merger(
    cli_env: dict[str, str], tmp_path: Path
) -> None:
    input_path = write_input(tmp_path)
    output_path = tmp_path / "out.mp3"
    result = invoke(
        [
            "convert",
            str(input_path),
            "-o",
            str(output_path),
            "--no-normalize",
            "--bitrate-kbps",
            "192",
            "--sleep",
            "0",
        ],
        cli_env,
    )
    assert result.exit_code == 0, result.output
    ffmpeg_log = Path(cli_env["FAKE_FFMPEG_LOG"]).read_text(encoding="utf-8")
    assert "loudnorm" not in ffmpeg_log
    assert "-b:a 192k" in ffmpeg_log


def test_convert_rejects_invalid_voice(cli_env: dict[str, str], tmp_path: Path) -> None:
    input_path = write_input(tmp_path)
    result = invoke(
        ["convert", str(input_path), "--voice", "Not-A-Voice", "--sleep", "0"],
        cli_env,
    )
    assert result.exit_code == 1
    assert "not installed" in result.output
    assert "Samantha" in result.output


def test_convert_missing_say_fails_fast(cli_env: dict[str, str], tmp_path: Path) -> None:
    input_path = write_input(tmp_path)
    env = dict(cli_env)
    env["TTS_SAY_BINARY"] = str(tmp_path / "does-not-exist")
    result = invoke(["convert", str(input_path), "--sleep", "0"], env)
    assert result.exit_code == 1
    assert "macOS `say` not found" in result.output


def test_resume_completed_job_is_idempotent(cli_env: dict[str, str], tmp_path: Path) -> None:
    input_path = write_input(tmp_path)
    output_path = tmp_path / "out.mp3"
    first = invoke(
        ["convert", str(input_path), "-o", str(output_path), "--sleep", "0"],
        cli_env,
    )
    assert first.exit_code == 0
    original = output_path.read_bytes()
    job_id = JsonJobStore(Path(cli_env["TTS_STATE_DIR"])).list_jobs()[0].job_id

    resumed = invoke(["resume", job_id], cli_env)
    assert resumed.exit_code == 0
    assert "already complete" in resumed.output
    assert output_path.read_bytes() == original


def test_pause_then_resume_via_cli(cli_env: dict[str, str], tmp_path: Path) -> None:
    input_path = write_input(tmp_path)
    output_path = tmp_path / "out.mp3"
    failing_env = dict(cli_env)
    failing_env["FAKE_SAY_FAIL"] = "1"

    failed = invoke(
        [
            "convert",
            str(input_path),
            "-o",
            str(output_path),
            "--sentences-per-chunk",
            "2",
            "--sleep",
            "0",
            "--retries",
            "0",
        ],
        failing_env,
    )
    assert failed.exit_code == 1
    assert "Paused:" in failed.output
    store = JsonJobStore(Path(cli_env["TTS_STATE_DIR"]))
    job = store.list_jobs()[0]
    assert job.status.value == "paused"
    assert not output_path.exists()

    resumed = invoke(["resume", job.job_id], cli_env)
    assert resumed.exit_code == 0, resumed.output
    assert "Done:" in resumed.output
    assert output_path.is_file()
    final_job = store.load(job.job_id)
    assert final_job is not None
    assert final_job.status.value == "completed"


def test_clean_removes_jobs(cli_env: dict[str, str], tmp_path: Path) -> None:
    input_path = write_input(tmp_path)
    result = invoke(
        ["convert", str(input_path), "-o", str(tmp_path / "out.mp3"), "--sleep", "0"],
        cli_env,
    )
    assert result.exit_code == 0

    kept = invoke(["clean", "--older-than", "7d"], cli_env)
    assert kept.exit_code == 0
    assert "Nothing to clean." in kept.output

    removed = invoke(["clean", "--older-than", "0s"], cli_env)
    assert removed.exit_code == 0
    assert "Removed 1 job(s)" in removed.output
    assert JsonJobStore(Path(cli_env["TTS_STATE_DIR"])).list_jobs() == []


def test_convert_resume_without_existing_job_starts_fresh(
    cli_env: dict[str, str], tmp_path: Path
) -> None:
    input_path = write_input(tmp_path)
    result = invoke(
        [
            "convert",
            str(input_path),
            "-o",
            str(tmp_path / "out.mp3"),
            "--resume",
            "--sleep",
            "0",
        ],
        cli_env,
    )
    assert result.exit_code == 0, result.output
    assert "No resumable job found" in result.output


def test_convert_from_stdin(cli_env: dict[str, str], tmp_path: Path) -> None:
    output_path = tmp_path / "stdin.mp3"
    before = set(Path(tempfile.gettempdir()).glob("tts-stdin-*"))
    result = runner.invoke(
        app,
        [
            "convert",
            "-",
            "--format",
            "txt",
            "-o",
            str(output_path),
            "--sleep",
            "0",
        ],
        env=cli_env,
        input="One sentence. Two sentence. Three sentence.",
    )
    after = set(Path(tempfile.gettempdir()).glob("tts-stdin-*"))
    assert result.exit_code == 0, result.output
    assert output_path.is_file()
    assert after == before

    job = JsonJobStore(Path(cli_env["TTS_STATE_DIR"])).list_jobs()[0]
    assert job.input_path is None
    assert job.title == "stdin"


def test_convert_stdin_requires_format(cli_env: dict[str, str]) -> None:
    result = runner.invoke(
        app,
        ["convert", "-", "--sleep", "0"],
        env=cli_env,
        input="hello",
    )
    assert result.exit_code == 1
    assert "requires --format" in result.output


def _chunk_count(output: str) -> int:
    for line in output.splitlines():
        if line.startswith("Chunks:"):
            return int(line.split()[1])
    raise AssertionError(f"no Chunks line in {output!r}")
