"""FFmpeg merger: normalize chunks to WAV, concat with silence, encode MP3."""

from __future__ import annotations

import tempfile
from pathlib import Path

from say_chunker.domain.errors import MergeError
from say_chunker.utils.subprocess_helpers import run_checked

_MERGE_TIMEOUT = 7200.0  # generous cap for full-book merges
_SAMPLE_RATE = 44100
_CHANNELS = 2
_LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"


class FFmpegMerger:
    def __init__(self, ffmpeg_binary: str = "ffmpeg") -> None:
        self.ffmpeg_binary = ffmpeg_binary

    def merge(
        self,
        segments: list[Path],
        output_path: Path,
        *,
        bitrate_kbps: int,
        normalize: bool,
        silence_between_ms: int,
    ) -> Path:
        if not segments:
            raise MergeError("no audio segments to merge")

        output_path.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="tts_merge_") as tmp_dir:
            tmp = Path(tmp_dir)
            wavs = [
                self._to_wav(segment, tmp / f"chunk_{i:05d}.wav")
                for i, segment in enumerate(segments)
            ]

            concat_list = tmp / "concat.txt"
            silence = None
            if silence_between_ms > 0:
                silence = self._make_silence(tmp / "silence.wav", silence_between_ms / 1000.0)
            _write_concat_list(concat_list, wavs, silence)

            self._concat_encode(concat_list, output_path, bitrate_kbps, normalize)

        return output_path

    def _to_wav(self, segment: Path, output: Path) -> Path:
        run_checked(
            [
                self.ffmpeg_binary,
                "-y",
                "-i",
                str(segment),
                "-ar",
                str(_SAMPLE_RATE),
                "-ac",
                str(_CHANNELS),
                "-c:a",
                "pcm_s16le",
                str(output),
            ],
            timeout_seconds=_MERGE_TIMEOUT,
            description=f"ffmpeg normalize {segment.name}",
        )
        return output

    def _make_silence(self, output: Path, seconds: float) -> Path:
        run_checked(
            [
                self.ffmpeg_binary,
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"anullsrc=r={_SAMPLE_RATE}:cl=stereo",
                "-t",
                f"{seconds:.3f}",
                "-c:a",
                "pcm_s16le",
                str(output),
            ],
            timeout_seconds=_MERGE_TIMEOUT,
            description="ffmpeg silence",
        )
        return output

    def _concat_encode(
        self,
        concat_list: Path,
        output_path: Path,
        bitrate_kbps: int,
        normalize: bool,
    ) -> None:
        args = [
            self.ffmpeg_binary,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c:a",
            "libmp3lame",
            "-b:a",
            f"{bitrate_kbps}k",
            "-id3v2_version",
            "3",
        ]
        if normalize:
            args.extend(["-af", _LOUDNORM])
        args.append(str(output_path))
        run_checked(
            args,
            timeout_seconds=_MERGE_TIMEOUT,
            description=f"ffmpeg encode {output_path.name}",
        )


def _write_concat_list(concat_list: Path, wavs: list[Path], silence: Path | None) -> None:
    lines: list[str] = []
    for i, wav in enumerate(wavs):
        lines.append(f"file '{_escape(wav)}'")
        if silence is not None and i < len(wavs) - 1:
            lines.append(f"file '{_escape(silence)}'")
    concat_list.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _escape(path: Path) -> str:
    return str(path.resolve()).replace("'", "'\\''")
