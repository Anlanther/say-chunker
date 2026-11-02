"""macOS ``say`` engine adapter.

Every chunk is a separate ``say`` subprocess writing an AIFF. Text is supplied
through ``-f <tempfile>`` rather than argv or stdin, avoiding length and quoting
problems. The child runs in its own session so a terminal Ctrl-C does not kill
the chunk mid-synthesis; the orchestrator pauses after the current chunk.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
from pathlib import Path

from say_chunker.domain.errors import (
    CommandError,
    CommandTimeoutError,
    InvalidVoiceError,
    MissingDependencyError,
    SynthesisError,
    SynthesisTimeoutError,
)
from say_chunker.domain.models import AudioSegment, Chunk
from say_chunker.utils.subprocess_helpers import run_command

logger = logging.getLogger(__name__)

_VOICE_LINE_RE = re.compile(r"^(?P<name>.+?)\s+(?P<locale>[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]+)*)$")


class SayEngine:
    name = "say"

    def __init__(self, say_path: Path = Path("/usr/bin/say")) -> None:
        resolved = _resolve_binary(say_path)
        if resolved is None:
            raise MissingDependencyError(
                f"macOS `say` not found at {say_path}. This tool requires macOS "
                "with the built-in `say` command at /usr/bin/say."
            )
        self._say_path = resolved
        self._voice_names: dict[str, str] = {}

    def list_voices(self) -> list[str]:
        try:
            result = run_command([str(self._say_path), "-v", "?"], timeout=30.0)
        except CommandError as exc:
            raise MissingDependencyError(
                f"Could not query available voices with `say -v '?'`: {exc}"
            ) from exc
        return _parse_voices(result.stdout)

    def validate_voice(self, voice: str) -> None:
        voices = self.list_voices()
        if voice in voices:
            self._voice_names[voice.casefold()] = voice
            return
        folded = {name.casefold(): name for name in voices}
        canonical = folded.get(voice.casefold())
        if canonical is not None:
            self._voice_names[voice.casefold()] = canonical
            return
        raise InvalidVoiceError(voice, voices)

    def synthesize(
        self,
        chunk: Chunk,
        output_path: Path,
        *,
        voice: str,
        rate: int,
        timeout_seconds: float,
    ) -> AudioSegment:
        if not chunk.text.strip():
            raise SynthesisError(f"Chunk {chunk.index} has no text to synthesize")

        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.unlink(missing_ok=True)
        except OSError as exc:
            raise SynthesisError(
                f"Cannot prepare output file {output_path} for chunk {chunk.index}: {exc}"
            ) from exc

        input_path: Path | None = None
        canonical_voice = self._voice_names.get(voice.casefold(), voice)
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                suffix=".txt",
                prefix=".say-input-",
                dir=str(output_path.parent),
                delete=False,
            ) as stream:
                input_path = Path(stream.name)
                stream.write(chunk.text)

            command = [
                str(self._say_path),
                "-v",
                canonical_voice,
                "-r",
                str(rate),
                "-f",
                str(input_path),
                "-o",
                str(output_path),
            ]
            logger.debug(
                "Synthesizing chunk %d (%d chars) with voice=%s rate=%d",
                chunk.index,
                chunk.char_count,
                canonical_voice,
                rate,
            )
            run_command(command, timeout=timeout_seconds, new_session=True)
        except CommandTimeoutError as exc:
            output_path.unlink(missing_ok=True)
            raise SynthesisTimeoutError(
                f"Chunk {chunk.index} timed out after {timeout_seconds:g}s"
            ) from exc
        except CommandError as exc:
            output_path.unlink(missing_ok=True)
            raise SynthesisError(f"say failed on chunk {chunk.index}: {exc}") from exc
        except OSError as exc:
            output_path.unlink(missing_ok=True)
            raise SynthesisError(f"Could not run say for chunk {chunk.index}: {exc}") from exc
        finally:
            if input_path is not None:
                input_path.unlink(missing_ok=True)

        try:
            size = output_path.stat().st_size
        except OSError as exc:
            raise SynthesisError(
                f"say did not create audio for chunk {chunk.index}: {output_path}"
            ) from exc
        if size == 0:
            raise SynthesisError(f"say produced an empty audio file for chunk {chunk.index}")

        return AudioSegment(chunk_index=chunk.index, path=output_path, duration_seconds=None)


def _resolve_binary(path: Path) -> Path | None:
    if path.is_absolute():
        if path.is_file() and os.access(path, os.X_OK):
            return path
        return None
    found = shutil.which(str(path))
    return Path(found) if found else None


def _parse_voices(output: str) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        prefix = line.split("#", maxsplit=1)[0].strip()
        match = _VOICE_LINE_RE.match(prefix)
        if match is not None:
            name = match.group("name").strip()
        else:
            parts = re.split(r"\s{2,}", line, maxsplit=2)
            name = parts[0].strip() if len(parts) >= 2 else ""
        if name and name not in seen:
            seen.add(name)
            names.append(name)

    return sorted(names, key=str.casefold)
