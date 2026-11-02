"""Application settings with CLI > env > TOML > default precedence."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from say_chunker.domain.models import SynthesisConfig
from say_chunker.utils.textutil import parse_duration


def default_config_path() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config_home) if config_home else Path.home() / ".config"
    return base / "say_chunker" / "config.toml"


def default_state_dir() -> Path:
    state_home = os.environ.get("XDG_STATE_HOME")
    base = Path(state_home) if state_home else Path.home() / ".local" / "state"
    return base / "say_chunker"


class AppSettings(BaseSettings):
    """Runtime settings.

    Environment variables use the ``TTS_`` prefix. The TOML file is
    ``$XDG_CONFIG_HOME/say_chunker/config.toml`` (or ``~/.config/say_chunker/config.toml``).
    ``TTS_CONFIG_FILE`` can point elsewhere.
    """

    model_config = SettingsConfigDict(
        env_prefix="TTS_",
        extra="ignore",
        validate_default=True,
    )

    voice: str = Field("Samantha", min_length=1)
    rate: int = Field(200, ge=50, le=600)
    sentences_per_chunk: int = Field(5, ge=1, le=100)
    max_sentence_chars: int = Field(2000, ge=50)
    language: str = Field("en", min_length=2, max_length=12)
    sleep_between_chunks: float = Field(0.5, ge=0, le=600)
    retries: int = Field(2, ge=0, le=10)
    normalize: bool = True
    bitrate_kbps: int = Field(128, ge=128, le=192)
    timeout_seconds: float = Field(300.0, gt=0)
    silence_between_chunks_ms: int = Field(250, ge=0, le=5000)
    max_runtime_seconds: float | None = Field(default=None, gt=0)

    # Binary overrides are useful for tests and unusual installs.
    say_binary: Path = Path("/usr/bin/say")
    ffmpeg_binary: str = "ffmpeg"
    ffprobe_binary: str = "ffprobe"
    state_dir: Path | None = None

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        config_path = Path(os.environ.get("TTS_CONFIG_FILE") or default_config_path())
        sources: list[PydanticBaseSettingsSource] = [init_settings, env_settings]
        if config_path.is_file():
            sources.append(TomlConfigSettingsSource(settings_cls, toml_file=config_path))
        sources.append(file_secret_settings)
        return tuple(sources)

    @field_validator("max_runtime_seconds", mode="before")
    @classmethod
    def _parse_max_runtime(cls, value: Any) -> Any:
        if isinstance(value, str):
            return parse_duration(value)
        return value

    @property
    def state_directory(self) -> Path:
        return self.state_dir if self.state_dir is not None else default_state_dir()

    def to_synthesis_config(self) -> SynthesisConfig:
        return SynthesisConfig(
            voice=self.voice,
            rate=self.rate,
            sentences_per_chunk=self.sentences_per_chunk,
            max_sentence_chars=self.max_sentence_chars,
            language=self.language,
            sleep_between_chunks=self.sleep_between_chunks,
            retries=self.retries,
            normalize=self.normalize,
            bitrate_kbps=self.bitrate_kbps,
            timeout_seconds=self.timeout_seconds,
            silence_between_chunks_ms=self.silence_between_chunks_ms,
            max_runtime_seconds=self.max_runtime_seconds,
        )
