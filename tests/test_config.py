"""Tests for config precedence and duration parsing."""

from __future__ import annotations

import pytest

import say_chunker.config as config_module
from say_chunker.config import Settings, load_settings, parse_duration


def test_defaults() -> None:
    settings = load_settings()
    assert settings.voice == "Samantha"
    assert settings.rate == 200
    assert settings.sentences_per_chunk == 5
    assert settings.max_sentence_chars == 2000
    assert settings.sleep_between_chunks == 0.5


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TTS_RATE", "300")
    assert load_settings().rate == 300


def test_cli_beats_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TTS_RATE", "300")
    assert load_settings({"rate": 250}).rate == 250


def test_env_beats_toml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module, "_read_toml_values", lambda: {"rate": 150})
    monkeypatch.setenv("TTS_RATE", "300")
    assert load_settings().rate == 300


def test_toml_beats_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module, "_read_toml_values", lambda: {"rate": 150})
    assert load_settings().rate == 150


def test_cli_beats_toml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module, "_read_toml_values", lambda: {"rate": 150})
    assert load_settings({"rate": 250}).rate == 250


def test_bool_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TTS_NORMALIZE", "false")
    assert load_settings().normalize is False


def test_parse_duration() -> None:
    assert parse_duration("2h") == 7200.0
    assert parse_duration("90s") == 90.0
    assert parse_duration("7d") == 604800.0
    assert parse_duration("1.5m") == 90.0
    assert parse_duration("42") == 42.0


def test_parse_duration_invalid() -> None:
    with pytest.raises(ValueError):
        parse_duration("nonsense")


def test_job_config_excludes_secrets() -> None:
    config = Settings(pdf_password="secret").job_config()
    assert "pdf_password" not in config
    assert "state_dir" not in config
    assert "say_binary" not in config
    assert "voice" in config
