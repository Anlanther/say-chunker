"""Tests for the ffmpeg merger using a fake ffmpeg binary."""

from __future__ import annotations

from pathlib import Path

from conftest import make_executable_script

from say_chunker.adapters.ffmpeg_merger import FFmpegMerger, _escape, _write_concat_list


def _make_fake_ffmpeg(tmp_path: Path, log: Path) -> Path:
    script = (
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        "args = sys.argv[1:]\n"
        f"with open({str(log)!r}, 'a') as f:\n"
        "    f.write(' '.join(args) + '\\n')\n"
        "out = args[-1]\n"
        "p = pathlib.Path(out)\n"
        "p.parent.mkdir(parents=True, exist_ok=True)\n"
        "p.write_bytes(b'FAKE')\n"
    )
    return make_executable_script(tmp_path / "ffmpeg", script)


def test_merge_creates_output_and_encodes_mp3(tmp_path: Path) -> None:
    log = tmp_path / "ffmpeg.log"
    ffmpeg = _make_fake_ffmpeg(tmp_path, log)

    segments = []
    for i in range(3):
        segment = tmp_path / f"in_{i}.aiff"
        segment.write_bytes(b"X")
        segments.append(segment)

    out = FFmpegMerger(str(ffmpeg)).merge(
        segments,
        tmp_path / "out.mp3",
        bitrate_kbps=192,
        normalize=True,
        silence_between_ms=250,
    )
    assert out.exists()
    text = log.read_text()
    assert "libmp3lame" in text
    assert "-b:a 192k" in text
    assert "loudnorm" in text
    assert "-f concat" in text


def test_merge_no_normalize(tmp_path: Path) -> None:
    log = tmp_path / "ffmpeg.log"
    ffmpeg = _make_fake_ffmpeg(tmp_path, log)

    segment = tmp_path / "in.aiff"
    segment.write_bytes(b"X")

    FFmpegMerger(str(ffmpeg)).merge(
        [segment],
        tmp_path / "out.mp3",
        bitrate_kbps=128,
        normalize=False,
        silence_between_ms=0,
    )
    text = log.read_text()
    assert "loudnorm" not in text
    assert "-b:a 128k" in text


def test_write_concat_list_with_silence(tmp_path: Path) -> None:
    wavs = [tmp_path / "a.wav", tmp_path / "b.wav", tmp_path / "c.wav"]
    silence = tmp_path / "s.wav"
    concat = tmp_path / "concat.txt"
    _write_concat_list(concat, wavs, silence)
    assert concat.read_text().strip().splitlines() == [
        f"file '{_escape(wavs[0])}'",
        f"file '{_escape(silence)}'",
        f"file '{_escape(wavs[1])}'",
        f"file '{_escape(silence)}'",
        f"file '{_escape(wavs[2])}'",
    ]


def test_write_concat_list_no_silence(tmp_path: Path) -> None:
    wavs = [tmp_path / "a.wav", tmp_path / "b.wav"]
    concat = tmp_path / "concat.txt"
    _write_concat_list(concat, wavs, None)
    assert concat.read_text().strip().splitlines() == [
        f"file '{_escape(wavs[0])}'",
        f"file '{_escape(wavs[1])}'",
    ]
