"""``say`` engine adapter: one subprocess per chunk, text fed via temp file."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from say_chunker.domain.errors import InvalidVoiceError, SynthesisError
from say_chunker.utils.subprocess_helpers import run_checked

_VOICE_LANGUAGE_RE = re.compile(r"\s+[a-z]{2}[-_][A-Za-z]{2}\b")


class SayEngine:
    def __init__(self, say_binary: Path = Path("/usr/bin/say")) -> None:
        self.say_binary = say_binary

    def synthesize(
        self,
        text: str,
        output_path: Path,
        *,
        voice: str,
        rate: int,
        timeout_seconds: float,
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_file: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", suffix=".txt", encoding="utf-8", delete=False
            ) as handle:
                handle.write(text)
                temp_file = Path(handle.name)

            run_checked(
                [
                    str(self.say_binary),
                    "-v",
                    voice,
                    "-r",
                    str(rate),
                    "-o",
                    str(output_path),
                    "-f",
                    str(temp_file),
                ],
                timeout_seconds=timeout_seconds,
                description=f"say -> {output_path.name}",
            )
        except SynthesisError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalize into a domain error
            raise SynthesisError(f"say failed: {exc}") from exc
        finally:
            if temp_file is not None:
                temp_file.unlink(missing_ok=True)
        return output_path


def list_voices(say_binary: Path = Path("/usr/bin/say")) -> list[str]:
    proc = run_checked(
        [str(say_binary), "-v", "?"],
        timeout_seconds=30.0,
        description="say -v '?'",
    )
    voices: list[str] = []
    for line in (proc.stdout or "").splitlines():
        name = _parse_voice_name(line)
        if name:
            voices.append(name)
    return voices


def validate_voice(voice: str, say_binary: Path = Path("/usr/bin/say")) -> None:
    voices = list_voices(say_binary)
    if voice not in voices:
        raise InvalidVoiceError(
            f"voice {voice!r} is not installed. Available voices: {', '.join(sorted(voices))}"
        )


def _parse_voice_name(line: str) -> str | None:
    match = _VOICE_LANGUAGE_RE.search(line)
    if not match:
        return None
    name = line[: match.start()].strip()
    return name or None
