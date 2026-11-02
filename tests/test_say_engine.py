"""Tests for the ``say`` engine adapter using a fake ``say`` binary."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import make_executable_script

from say_chunker.adapters.say_engine import (
    SayEngine,
    _parse_voice_name,
    list_voices,
    validate_voice,
)
from say_chunker.domain.errors import InvalidVoiceError, SynthesisError


def _make_fake_say(tmp_path: Path, log_path: Path, behavior: str = "ok") -> Path:
    script = (
        "#!/usr/bin/env python3\n"
        "import pathlib, sys, time\n"
        "args = sys.argv[1:]\n"
        f"with open({str(log_path)!r}, 'a') as f:\n"
        "    f.write(' '.join(args) + '\\n')\n"
        f"if {behavior!r} == 'sleep':\n"
        "    time.sleep(10)\n"
        f"if {behavior!r} == 'fail':\n"
        "    sys.exit(1)\n"
        "out = None\n"
        "for i, a in enumerate(args):\n"
        "    if a == '-o' and i + 1 < len(args):\n"
        "        out = args[i + 1]\n"
        "if out:\n"
        "    p = pathlib.Path(out)\n"
        "    p.parent.mkdir(parents=True, exist_ok=True)\n"
        "    p.write_bytes(b'FAKEAIFF')\n"
    )
    return make_executable_script(tmp_path / "say", script)


def test_synthesize_writes_output_and_uses_tempfile(tmp_path: Path) -> None:
    log = tmp_path / "say.log"
    engine = SayEngine(_make_fake_say(tmp_path, log))
    out = engine.synthesize(
        "Hello world. This is a longer piece of text.",
        tmp_path / "out" / "chunk.aiff",
        voice="Samantha",
        rate=200,
        timeout_seconds=5,
    )
    assert out.exists()
    argv = log.read_text().strip()
    assert "-f" in argv  # text is fed via a temp file, not argv
    assert "Hello world" not in argv
    assert "-o" in argv


def test_synthesize_timeout(tmp_path: Path) -> None:
    log = tmp_path / "say.log"
    engine = SayEngine(_make_fake_say(tmp_path, log, behavior="sleep"))
    with pytest.raises(SynthesisError):
        engine.synthesize(
            "x", tmp_path / "out.aiff", voice="Samantha", rate=200, timeout_seconds=0.5
        )


def test_synthesize_nonzero(tmp_path: Path) -> None:
    log = tmp_path / "say.log"
    engine = SayEngine(_make_fake_say(tmp_path, log, behavior="fail"))
    with pytest.raises(SynthesisError):
        engine.synthesize("x", tmp_path / "out.aiff", voice="Samantha", rate=200, timeout_seconds=5)


def test_list_voices_and_validate(tmp_path: Path) -> None:
    script = (
        "#!/usr/bin/env python3\n"
        'print("Alex                en_US    # comment")\n'
        'print("Samantha            en_US    # comment")\n'
        'print("Good News           en_GB    # comment")\n'
    )
    say = make_executable_script(tmp_path / "say", script)
    voices = list_voices(say)
    assert "Alex" in voices
    assert "Samantha" in voices
    assert "Good News" in voices

    validate_voice("Samantha", say)
    with pytest.raises(InvalidVoiceError):
        validate_voice("Nope", say)


def test_parse_voice_name() -> None:
    assert _parse_voice_name("Alex                en_US    # comment") == "Alex"
    assert _parse_voice_name("Good News           en_GB    # comment") == "Good News"
    assert _parse_voice_name("garbage line without a language code") is None
