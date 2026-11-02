from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from say_chunker.adapters.say_engine import SayEngine
from say_chunker.domain.errors import (
    InvalidVoiceError,
    MissingDependencyError,
    SynthesisError,
    SynthesisTimeoutError,
)
from say_chunker.domain.models import Chunk
from tests.fake_bins import (
    FAKE_SAY_VOICES,
    write_executable,
    write_fake_say,
)


@pytest.fixture
def fake_say(tmp_path: Path) -> Path:
    return write_fake_say(tmp_path)


def chunk(text: str = "Hello world. This is a test.", index: int = 0) -> Chunk:
    return Chunk(
        index=index,
        section_index=0,
        section_title=None,
        sentences=(),
        start_sentence_index=0,
        end_sentence_index=0,
        text=text,
        char_count=len(text),
    )


def test_missing_say_binary(tmp_path: Path) -> None:
    with pytest.raises(MissingDependencyError, match="macOS `say`"):
        SayEngine(say_path=tmp_path / "nope")


def test_list_voices_parses_names_with_spaces(fake_say: Path, tmp_path: Path) -> None:
    voices_file = tmp_path / "voices.txt"
    voices_file.write_text(FAKE_SAY_VOICES, encoding="utf-8")
    os.environ["FAKE_SAY_VOICES_FILE"] = str(voices_file)
    try:
        engine = SayEngine(say_path=fake_say)
        assert engine.list_voices() == [
            "Alex",
            "Bad News",
            "Daniel",
            "Grandma (English (US))",
            "Samantha",
        ]
    finally:
        os.environ.pop("FAKE_SAY_VOICES_FILE", None)


def test_validate_voice_success_case_insensitive(fake_say: Path) -> None:
    engine = SayEngine(say_path=fake_say)
    engine.validate_voice("Samantha")
    engine.validate_voice("samantha")


def test_validate_voice_failure_lists_voices(fake_say: Path) -> None:
    engine = SayEngine(say_path=fake_say)
    with pytest.raises(InvalidVoiceError) as error:
        engine.validate_voice("Not-A-Voice")
    assert "Samantha" in str(error.value)
    assert "tts voices" in str(error.value)


def test_synthesize_uses_file_input_and_cleans_up(fake_say: Path, tmp_path: Path) -> None:
    log_path = tmp_path / "say.log"
    os.environ["FAKE_SAY_LOG"] = str(log_path)
    try:
        engine = SayEngine(say_path=fake_say)
        output = tmp_path / "chunk.aiff"
        segment = engine.synthesize(
            chunk(),
            output,
            voice="Samantha",
            rate=200,
            timeout_seconds=10.0,
        )
    finally:
        os.environ.pop("FAKE_SAY_LOG", None)

    assert segment.chunk_index == 0
    assert segment.path == output
    assert output.read_text(encoding="utf-8") == "Hello world. This is a test."
    line = log_path.read_text(encoding="utf-8").strip()
    voice, rate, input_file, output_file = line.split("|")
    assert voice == "Samantha"
    assert rate == "200"
    assert input_file.endswith(".txt")
    assert output_file == str(output)
    assert not list(tmp_path.glob(".say-input-*"))


def test_synthesize_uses_canonical_voice_case(
    fake_say: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_path = tmp_path / "say.log"
    monkeypatch.setenv("FAKE_SAY_LOG", str(log_path))
    engine = SayEngine(say_path=fake_say)
    engine.validate_voice("samantha")
    engine.synthesize(
        chunk(),
        tmp_path / "chunk.aiff",
        voice="samantha",
        rate=200,
        timeout_seconds=10.0,
    )
    voice, _, _, _ = log_path.read_text(encoding="utf-8").strip().split("|")
    assert voice == "Samantha"


def test_synthesize_timeout_kills_process_and_cleans_temp(fake_say: Path, tmp_path: Path) -> None:
    os.environ["FAKE_SAY_SLEEP"] = "5"
    try:
        engine = SayEngine(say_path=fake_say)
        output = tmp_path / "timeout.aiff"
        started = time.monotonic()
        with pytest.raises(SynthesisTimeoutError, match="timed out"):
            engine.synthesize(
                chunk(),
                output,
                voice="Samantha",
                rate=200,
                timeout_seconds=0.2,
            )
        elapsed = time.monotonic() - started
    finally:
        os.environ.pop("FAKE_SAY_SLEEP", None)

    assert elapsed < 3.0
    assert not output.exists()
    assert not list(tmp_path.glob(".say-input-*"))


def test_synthesize_nonzero_exit(fake_say: Path, tmp_path: Path) -> None:
    os.environ["FAKE_SAY_FAIL"] = "1"
    try:
        engine = SayEngine(say_path=fake_say)
        with pytest.raises(SynthesisError, match="say failed"):
            engine.synthesize(
                chunk(),
                tmp_path / "failed.aiff",
                voice="Samantha",
                rate=200,
                timeout_seconds=10.0,
            )
    finally:
        os.environ.pop("FAKE_SAY_FAIL", None)
    assert not (tmp_path / "failed.aiff").exists()


def test_synthesize_missing_output(fake_say: Path, tmp_path: Path) -> None:
    os.environ["FAKE_SAY_NO_OUTPUT"] = "1"
    try:
        engine = SayEngine(say_path=fake_say)
        with pytest.raises(SynthesisError, match="did not create audio"):
            engine.synthesize(
                chunk(),
                tmp_path / "missing.aiff",
                voice="Samantha",
                rate=200,
                timeout_seconds=10.0,
            )
    finally:
        os.environ.pop("FAKE_SAY_NO_OUTPUT", None)


def test_synthesize_rejects_empty_text(fake_say: Path, tmp_path: Path) -> None:
    engine = SayEngine(say_path=fake_say)
    with pytest.raises(SynthesisError, match="no text"):
        engine.synthesize(
            chunk("   "),
            tmp_path / "empty.aiff",
            voice="Samantha",
            rate=200,
            timeout_seconds=10.0,
        )


def test_parse_voices_ignores_blank_and_malformed_lines(fake_say: Path) -> None:
    script = fake_say.read_text(encoding="utf-8").replace(
        "printf '%s\\n' \"Samantha            en_US    # Hello\"",
        'printf \'%s\\n\' "" "Weird" "Samantha            en_US    # Hello"',
    )
    write_executable(fake_say, script)
    assert SayEngine(say_path=fake_say).list_voices() == ["Samantha"]
