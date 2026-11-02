from __future__ import annotations

from pathlib import Path

import pytest

from say_chunker.adapters.ffmpeg_merger import FFmpegMerger
from say_chunker.domain.errors import MergeError, MissingDependencyError
from say_chunker.domain.models import AudioSegment
from tests.fake_bins import write_fake_ffmpeg, write_fake_ffprobe


@pytest.fixture
def fake_ffmpeg(tmp_path: Path) -> tuple[Path, Path]:
    return write_fake_ffmpeg(tmp_path), write_fake_ffprobe(tmp_path)


def segments(tmp_path: Path, count: int = 3) -> list[AudioSegment]:
    result: list[AudioSegment] = []
    for index in range(count):
        path = tmp_path / f"chunk_{index}.aiff"
        path.write_bytes(b"AIFF" + bytes([index]))
        result.append(AudioSegment(chunk_index=index, path=path))
    return result


def test_missing_ffmpeg(tmp_path: Path) -> None:
    merger = FFmpegMerger(ffmpeg_path=tmp_path / "missing", ffprobe_path="ffprobe")
    with pytest.raises(MissingDependencyError, match="brew install ffmpeg"):
        merger.validate()


def test_missing_ffprobe(tmp_path: Path, fake_ffmpeg: tuple[Path, Path]) -> None:
    ffmpeg, _ = fake_ffmpeg
    merger = FFmpegMerger(ffmpeg_path=ffmpeg, ffprobe_path=tmp_path / "missing")
    with pytest.raises(MissingDependencyError, match="ffprobe"):
        merger.validate()


def test_merge_with_silence_and_loudnorm(
    tmp_path: Path,
    fake_ffmpeg: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ffmpeg, ffprobe = fake_ffmpeg
    log_path = tmp_path / "ffmpeg.log"
    list_log = tmp_path / "concat.log"
    monkeypatch.setenv("FAKE_FFMPEG_LOG", str(log_path))
    monkeypatch.setenv("FAKE_FFMPEG_LIST_LOG", str(list_log))

    output = tmp_path / "out" / "book.mp3"
    merger = FFmpegMerger(ffmpeg_path=ffmpeg, ffprobe_path=ffprobe)
    merger.validate()
    merger.merge(
        segments(tmp_path),
        output,
        normalize=True,
        bitrate_kbps=128,
        silence_ms=250,
    )

    assert output.read_text(encoding="utf-8") == "fake audio"
    log = log_path.read_text(encoding="utf-8")
    assert "anullsrc=r=22050:cl=mono" in log
    assert "loudnorm=I=-16:TP=-1.5:LRA=11" in log
    assert "-b:a 128k" in log
    assert "-ar 44100" in log
    assert "concat" in log

    concat = list_log.read_text(encoding="utf-8")
    assert concat.splitlines() == [
        "file 'seg_00000.aiff'",
        "file 'silence.aiff'",
        "file 'seg_00001.aiff'",
        "file 'silence.aiff'",
        "file 'seg_00002.aiff'",
    ]
    assert not list(tmp_path.glob(".tts-merge-*"))


def test_merge_no_normalize_no_silence(
    tmp_path: Path,
    fake_ffmpeg: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ffmpeg, ffprobe = fake_ffmpeg
    log_path = tmp_path / "ffmpeg.log"
    list_log = tmp_path / "concat.log"
    monkeypatch.setenv("FAKE_FFMPEG_LOG", str(log_path))
    monkeypatch.setenv("FAKE_FFMPEG_LIST_LOG", str(list_log))

    output = tmp_path / "book.mp3"
    merger = FFmpegMerger(ffmpeg_path=ffmpeg, ffprobe_path=ffprobe)
    merger.merge(
        segments(tmp_path, count=2),
        output,
        normalize=False,
        bitrate_kbps=192,
        silence_ms=0,
    )

    log = log_path.read_text(encoding="utf-8")
    assert "loudnorm" not in log
    assert "anullsrc" not in log
    assert "-b:a 192k" in log
    concat = list_log.read_text(encoding="utf-8")
    assert concat.splitlines() == ["file 'seg_00000.aiff'", "file 'seg_00001.aiff'"]


def test_merge_failure_cleans_temp_and_keeps_old_output(
    tmp_path: Path,
    fake_ffmpeg: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ffmpeg, ffprobe = fake_ffmpeg
    output = tmp_path / "book.mp3"
    output.write_text("old", encoding="utf-8")
    monkeypatch.setenv("FAKE_FFMPEG_FAIL_CONCAT", "1")

    merger = FFmpegMerger(ffmpeg_path=ffmpeg, ffprobe_path=ffprobe)
    with pytest.raises(MergeError, match="ffmpeg failed"):
        merger.merge(
            segments(tmp_path, count=2),
            output,
            normalize=True,
            bitrate_kbps=128,
            silence_ms=0,
        )

    assert output.read_text(encoding="utf-8") == "old"
    assert not list(tmp_path.glob(".tts-merge-*"))


def test_merge_rejects_empty_segments(tmp_path: Path, fake_ffmpeg: tuple[Path, Path]) -> None:
    ffmpeg, ffprobe = fake_ffmpeg
    merger = FFmpegMerger(ffmpeg_path=ffmpeg, ffprobe_path=ffprobe)
    with pytest.raises(MergeError, match="No audio segments"):
        merger.merge([], tmp_path / "out.mp3", normalize=True, bitrate_kbps=128, silence_ms=250)


def test_merge_rejects_missing_segment(tmp_path: Path, fake_ffmpeg: tuple[Path, Path]) -> None:
    ffmpeg, ffprobe = fake_ffmpeg
    merger = FFmpegMerger(ffmpeg_path=ffmpeg, ffprobe_path=ffprobe)
    with pytest.raises(MergeError, match="Missing chunk audio"):
        merger.merge(
            [AudioSegment(chunk_index=0, path=tmp_path / "not-there.aiff")],
            tmp_path / "out.mp3",
            normalize=True,
            bitrate_kbps=128,
            silence_ms=250,
        )


def test_merge_rejects_bad_bitrate(tmp_path: Path, fake_ffmpeg: tuple[Path, Path]) -> None:
    ffmpeg, ffprobe = fake_ffmpeg
    merger = FFmpegMerger(ffmpeg_path=ffmpeg, ffprobe_path=ffprobe)
    with pytest.raises(MergeError, match="bitrate"):
        merger.merge(
            segments(tmp_path, count=1),
            tmp_path / "out.mp3",
            normalize=True,
            bitrate_kbps=999,
            silence_ms=0,
        )
