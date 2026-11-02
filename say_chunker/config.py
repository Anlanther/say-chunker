"""Configuration via ``pydantic-settings``.

Precedence (highest first):

1. CLI flags (passed as a mapping of overrides)
2. ``TTS_*`` environment variables
3. ``~/.config/say_chunker/config.toml``
4. field defaults

``pydantic-settings`` provides the model + validation; we implement the layered
precedence explicitly so a TOML file sits *below* environment variables (which is
not the native ``init kwargs`` ordering).
"""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path
from typing import Any, Mapping

from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    # Synthesis identity (these four feed the job id / chunking).
    voice: str = "Samantha"
    rate: int = 200
    sentences_per_chunk: int = 5
    language: str = "en"

    # Chunking.
    max_sentence_chars: int = 2000

    # Runtime knobs.
    sleep_between_chunks: float = 0.5
    retries: int = 2
    chunk_timeout: float = 300.0
    max_runtime: str | None = None

    # Merge.
    normalize: bool = True
    bitrate_kbps: int = 192
    silence_between_ms: int = 250

    # Input handling.
    format: str | None = None
    pdf_password: str | None = None

    # External binaries / storage.
    say_binary: str = "/usr/bin/say"
    ffmpeg_binary: str = "ffmpeg"
    ffprobe_binary: str = "ffprobe"
    state_dir: str = "~/.local/state/say_chunker"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Environment/TOML precedence is handled explicitly in ``load_settings``;
        # keep pydantic-settings from double-reading the process environment.
        del settings_cls, env_settings, dotenv_settings, file_secret_settings
        return (init_settings,)

    def job_config(self) -> dict[str, Any]:
        """Return the subset of settings that must be captured for a job."""
        data = self.model_dump()
        for key in (
            "format",
            "pdf_password",
            "say_binary",
            "ffmpeg_binary",
            "ffprobe_binary",
            "state_dir",
        ):
            data.pop(key, None)
        return data


def _coerce_env(raw: str, default: Any) -> Any:
    if isinstance(default, bool):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(default, int):
        return int(raw)
    if isinstance(default, float):
        return float(raw)
    if default is None:
        return raw or None
    return raw


def _read_env_values() -> dict[str, Any]:
    defaults = Settings()
    values: dict[str, Any] = {}
    for name in Settings.model_fields:
        env_key = f"TTS_{name.upper()}"
        raw = os.environ.get(env_key)
        if raw is None:
            continue
        values[name] = _coerce_env(raw, getattr(defaults, name))
    return values


def _read_toml_values() -> dict[str, Any]:
    path = Path("~/.config/say_chunker/config.toml").expanduser()
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    known = set(Settings.model_fields)
    return {key: value for key, value in data.items() if key in known}


def load_settings(cli_overrides: Mapping[str, Any] | None = None) -> Settings:
    """Build settings honouring CLI > env > TOML > defaults."""
    overrides = {k: v for k, v in (cli_overrides or {}).items() if v is not None}
    merged = {**_read_toml_values(), **_read_env_values(), **overrides}
    return Settings(**merged)


_DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(s|m|h|d)?$", re.IGNORECASE)
_UNITS = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}


def parse_duration(value: str) -> float:
    """Parse a human duration like ``2h``, ``90s``, ``1.5m``, ``7d`` into seconds."""
    value = value.strip()
    match = _DURATION_RE.match(value)
    if not match:
        raise ValueError(f"invalid duration: {value!r} (expected e.g. 2h, 90s, 7d)")
    amount = float(match.group(1))
    unit = (match.group(2) or "s").lower()
    return amount * _UNITS[unit]
