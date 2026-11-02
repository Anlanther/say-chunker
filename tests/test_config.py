from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from say_chunker.config import AppSettings, default_state_dir


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in (
        "TTS_RATE",
        "TTS_VOICE",
        "TTS_SENTENCES_PER_CHUNK",
        "TTS_NORMALIZE",
        "TTS_MAX_RUNTIME_SECONDS",
        "TTS_STATE_DIR",
        "TTS_SAY_BINARY",
        "XDG_STATE_HOME",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TTS_CONFIG_FILE", str(tmp_path / "missing.toml"))


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AppSettings()
    assert settings.voice == "Samantha"
    assert settings.rate == 200
    assert settings.sentences_per_chunk == 5
    assert settings.max_sentence_chars == 2000
    assert settings.sleep_between_chunks == 0.5
    assert settings.normalize is True
    assert settings.bitrate_kbps == 128
    assert settings.timeout_seconds == 300.0


def test_toml_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        'voice = "Daniel"\nrate = 180\nsentences_per_chunk = 7\n'
        'sleep_between_chunks = 0.75\nmax_runtime_seconds = "2h"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("TTS_CONFIG_FILE", str(config))
    settings = AppSettings()
    assert settings.voice == "Daniel"
    assert settings.rate == 180
    assert settings.sentences_per_chunk == 7
    assert settings.sleep_between_chunks == 0.75
    assert settings.max_runtime_seconds == 7200.0


def test_env_overrides_toml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('voice = "Daniel"\nrate = 180\n', encoding="utf-8")
    monkeypatch.setenv("TTS_CONFIG_FILE", str(config))
    monkeypatch.setenv("TTS_RATE", "190")
    monkeypatch.setenv("TTS_NORMALIZE", "false")
    settings = AppSettings()
    assert settings.voice == "Daniel"
    assert settings.rate == 190
    assert settings.normalize is False


def test_init_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TTS_RATE", "190")
    monkeypatch.setenv("TTS_VOICE", "Alex")
    settings = AppSettings(rate=220, voice="Samantha")
    assert settings.rate == 220
    assert settings.voice == "Samantha"


def test_state_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", "/custom/state")
    assert default_state_dir() == Path("/custom/state/say_chunker")
    assert AppSettings().state_directory == Path("/custom/state/say_chunker")
    assert AppSettings(state_dir=Path("/explicit")).state_directory == Path("/explicit")


def test_to_synthesis_config() -> None:
    settings = AppSettings(voice="Alex", rate=210, normalize=False)
    config = settings.to_synthesis_config()
    assert config.voice == "Alex"
    assert config.rate == 210
    assert config.normalize is False


def test_invalid_values_rejected() -> None:
    with pytest.raises(ValidationError):
        AppSettings(rate=0)
    with pytest.raises(ValidationError):
        AppSettings(sentences_per_chunk=0)
    with pytest.raises(ValidationError):
        AppSettings(bitrate_kbps=64)
    with pytest.raises(ValidationError):
        AppSettings(timeout_seconds=0)


def test_duration_string_for_max_runtime() -> None:
    assert AppSettings(max_runtime_seconds="90m").max_runtime_seconds == 5400.0
