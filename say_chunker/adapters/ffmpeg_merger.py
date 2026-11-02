"""ffmpeg adapter: concatenate AIFF chunks and encode a single MP3."""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from pathlib import Path

from say_chunker.domain.errors import CommandError, MergeError, MissingDependencyError
from say_chunker.domain.models import AudioSegment
from say_chunker.utils.subprocess_helpers import run_command

logger = logging.getLogger(__name__)

_LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"


class FFmpegMerger:
    name = "ffmpeg"

    def __init__(
        self,
        ffmpeg_path: str | Path = "ffmpeg",
        ffprobe_path: str | Path = "ffprobe",
    ) -> None:
        self._ffmpeg_arg = str(ffmpeg_path)
        self._ffprobe_arg = str(ffprobe_path)
        self._ffmpeg: Path | None = None
        self._ffprobe: Path | None = None

    def validate(self) -> None:
        self._ffmpeg = _require_binary(self._ffmpeg_arg, "ffmpeg")
        self._ffprobe = _require_binary(self._ffprobe_arg, "ffprobe")
        for binary in (self._ffmpeg, self._ffprobe):
            try:
                run_command([str(binary), "-version"], timeout=15.0)
            except CommandError as exc:
                raise MissingDependencyError(
                    f"{binary.name} is present but could not be executed: {exc}"
                ) from exc

    def merge(
        self,
        segments: list[AudioSegment],
        output_path: Path,
        *,
        normalize: bool,
        bitrate_kbps: int,
        silence_ms: int,
    ) -> None:
        if not segments:
            raise MergeError("No audio segments to merge")
        if silence_ms < 0:
            raise MergeError("Silence duration cannot be negative")
        if not 8 <= bitrate_kbps <= 320:
            raise MergeError(f"Unsupported MP3 bitrate: {bitrate_kbps} kbps")

        if self._ffmpeg is None or self._ffprobe is None:
            self.validate()
        assert self._ffmpeg is not None
        assert self._ffprobe is not None

        missing = [segment.path for segment in segments if not segment.path.is_file()]
        if missing:
            raise MergeError(f"Missing chunk audio file(s): {', '.join(map(str, missing))}")

        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise MergeError(f"Cannot create output directory {output_path.parent}: {exc}") from exc

        try:
            merge_dir = Path(tempfile.mkdtemp(prefix=".tts-merge-", dir=str(output_path.parent)))
        except OSError as exc:
            raise MergeError(
                f"Cannot create a temporary merge directory next to {output_path}: {exc}"
            ) from exc
        try:
            _merge_in_dir(
                segments=segments,
                output_path=output_path,
                merge_dir=merge_dir,
                ffmpeg=self._ffmpeg,
                ffprobe=self._ffprobe,
                normalize=normalize,
                bitrate_kbps=bitrate_kbps,
                silence_ms=silence_ms,
            )
        except MergeError:
            raise
        except OSError as exc:
            raise MergeError(f"Filesystem error while merging {output_path}: {exc}") from exc
        finally:
            shutil.rmtree(merge_dir, ignore_errors=True)


def _merge_in_dir(
    *,
    segments: list[AudioSegment],
    output_path: Path,
    merge_dir: Path,
    ffmpeg: Path,
    ffprobe: Path,
    normalize: bool,
    bitrate_kbps: int,
    silence_ms: int,
) -> None:
    segment_files: list[Path] = []
    for index, segment in enumerate(segments):
        target = merge_dir / f"seg_{index:05d}.aiff"
        try:
            os.symlink(segment.path.resolve(), target)
        except OSError:
            try:
                shutil.copy2(segment.path, target)
            except OSError as exc:
                raise MergeError(f"Cannot stage audio segment {segment.path}: {exc}") from exc
        segment_files.append(target)

    sample_rate, channels, codec = _probe_audio(segment_files[0], ffprobe)
    silence_file: Path | None = None
    if silence_ms > 0 and len(segment_files) > 1:
        silence_file = merge_dir / "silence.aiff"
        _generate_silence(
            silence_file,
            ffmpeg=ffmpeg,
            sample_rate=sample_rate,
            channels=channels,
            codec=codec,
            seconds=silence_ms / 1000.0,
        )

    list_file = merge_dir / "concat.txt"
    lines: list[str] = []
    for index, segment_file in enumerate(segment_files):
        lines.append(f"file '{segment_file.name}'")
        if silence_file is not None and index < len(segment_files) - 1:
            lines.append(f"file '{silence_file.name}'")
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    temporary_output = merge_dir / "output.mp3"
    command = [
        str(ffmpeg),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-vn",
        "-c:a",
        "libmp3lame",
        "-b:a",
        f"{bitrate_kbps}k",
    ]
    if normalize:
        command.extend(["-af", _LOUDNORM])
    command.extend(["-ar", "44100", str(temporary_output)])

    try:
        run_command(command, timeout=3600.0)
    except CommandError as exc:
        raise MergeError(f"ffmpeg failed to create {output_path}: {exc}") from exc

    if not temporary_output.is_file() or temporary_output.stat().st_size == 0:
        raise MergeError(f"ffmpeg did not produce a valid MP3 at {temporary_output}")
    _verify_audio_stream(temporary_output, ffprobe)

    try:
        os.replace(temporary_output, output_path)
    except OSError as exc:
        raise MergeError(f"Cannot write output MP3 {output_path}: {exc}") from exc

    logger.info(
        "Merged %d chunk(s) into %s (bitrate=%dk, normalize=%s, silence=%dms)",
        len(segments),
        output_path,
        bitrate_kbps,
        normalize,
        silence_ms,
    )


def _probe_audio(path: Path, ffprobe: Path) -> tuple[int, int, str]:
    try:
        result = run_command(
            [
                str(ffprobe),
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=sample_rate,channels,codec_name",
                "-of",
                "json",
                str(path),
            ],
            timeout=30.0,
        )
        payload = json.loads(result.stdout)
        stream = payload["streams"][0]
        sample_rate = int(stream.get("sample_rate", 22050))
        channels = int(stream.get("channels", 1))
        codec = str(stream.get("codec_name", "pcm_s16be"))
        if not codec.startswith("pcm_"):
            codec = "pcm_s16be"
        return sample_rate, channels, codec
    except (
        CommandError,
        json.JSONDecodeError,
        KeyError,
        IndexError,
        TypeError,
        ValueError,
        AttributeError,
    ) as exc:
        logger.warning("Could not probe %s with ffprobe (%s); assuming 22050 Hz mono", path, exc)
        return 22050, 1, "pcm_s16be"


def _verify_audio_stream(path: Path, ffprobe: Path) -> None:
    try:
        result = run_command(
            [
                str(ffprobe),
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_name",
                "-of",
                "json",
                str(path),
            ],
            timeout=30.0,
        )
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict):
            raise MergeError(f"ffprobe returned unexpected data for {path}")
        streams = payload.get("streams")
        if not isinstance(streams, list) or not streams:
            raise MergeError(f"ffprobe found no audio stream in {path}")
    except (CommandError, json.JSONDecodeError, TypeError) as exc:
        raise MergeError(f"Could not verify the merged MP3 {path}: {exc}") from exc


def _generate_silence(
    silence_file: Path,
    *,
    ffmpeg: Path,
    sample_rate: int,
    channels: int,
    codec: str,
    seconds: float,
) -> None:
    layout = "mono" if channels == 1 else "stereo" if channels == 2 else ""
    source = f"anullsrc=r={sample_rate}"
    if layout:
        source += f":cl={layout}"
    command = [
        str(ffmpeg),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        source,
        "-t",
        f"{seconds:g}",
        "-c:a",
        codec,
        "-ar",
        str(sample_rate),
        "-ac",
        str(channels),
        str(silence_file),
    ]
    try:
        run_command(command, timeout=120.0)
    except CommandError as exc:
        raise MergeError(f"ffmpeg failed to generate inter-chunk silence: {exc}") from exc


def _require_binary(value: str, name: str) -> Path:
    path = Path(value)
    if path.is_absolute() or "/" in value:
        if path.is_file() and os.access(path, os.X_OK):
            return path
        raise MissingDependencyError(
            f"{name} not found at {value}. Install it with: brew install ffmpeg"
        )
    found = shutil.which(value)
    if found is None:
        raise MissingDependencyError(
            f"{name} not found on PATH. Install it with: brew install ffmpeg"
        )
    return Path(found)
