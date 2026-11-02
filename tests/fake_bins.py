"""Helpers that write executable fake `say`, `ffmpeg`, and `ffprobe` scripts."""

from __future__ import annotations

from pathlib import Path

FAKE_SAY_VOICES = """Samantha            en_US    # Hello, my name is Samantha.
Alex                en_US    # Hello, my name is Alex.
Bad News            en_US    # This voice name has a single space.
Daniel              en_GB    # Hello, my name is Daniel.
Grandma (English (US)) en_US    # A long name with only one separator space.
"""

FAKE_SAY = """#!/bin/sh
if [ "$1" = "-v" ] && [ "$2" = "?" ]; then
    if [ -n "$FAKE_SAY_VOICES_FILE" ] && [ -f "$FAKE_SAY_VOICES_FILE" ]; then
        cat "$FAKE_SAY_VOICES_FILE"
    else
        printf '%s\\n' "Samantha            en_US    # Hello"
    fi
    exit 0
fi

input=""
output=""
voice=""
rate=""
while [ $# -gt 0 ]; do
    case "$1" in
        -f) input="$2"; shift 2 ;;
        -o) output="$2"; shift 2 ;;
        -v) voice="$2"; shift 2 ;;
        -r) rate="$2"; shift 2 ;;
        *) shift ;;
    esac
done

if [ -n "$FAKE_SAY_LOG" ]; then
    printf '%s|%s|%s|%s\\n' "$voice" "$rate" "$input" "$output" >> "$FAKE_SAY_LOG"
fi
if [ -n "$FAKE_SAY_SLEEP" ]; then
    sleep "$FAKE_SAY_SLEEP"
fi
if [ "${FAKE_SAY_FAIL:-0}" = "1" ]; then
    printf 'fake say failure\\n' >&2
    exit 3
fi
if [ "${FAKE_SAY_NO_OUTPUT:-0}" = "1" ]; then
    exit 0
fi
if [ -n "$output" ]; then
    cp "$input" "$output"
fi
exit 0
"""

FAKE_FFMPEG = """#!/bin/sh
if [ "$1" = "-version" ]; then
    printf 'ffmpeg version fake\\n'
    exit 0
fi

if [ -n "$FAKE_FFMPEG_LOG" ]; then
    printf '%s\\n' "$*" >> "$FAKE_FFMPEG_LOG"
fi

output=""
previous=""
for arg in "$@"; do
    if [ "$previous" = "-i" ] && [ -f "$arg" ]; then
        if [ -n "$FAKE_FFMPEG_LIST_LOG" ]; then
            cat "$arg" >> "$FAKE_FFMPEG_LIST_LOG"
        fi
    fi
    previous="$arg"
    output="$arg"
done

case "$*" in
    *concat*)
        if [ "${FAKE_FFMPEG_FAIL_CONCAT:-0}" = "1" ]; then
            printf 'fake ffmpeg concat failure\\n' >&2
            exit 2
        fi
        ;;
esac

if [ "${FAKE_FFMPEG_FAIL:-0}" = "1" ]; then
    printf 'fake ffmpeg failure\\n' >&2
    exit 2
fi

if [ -n "$output" ]; then
    printf 'fake audio' > "$output"
fi
exit 0
"""

FAKE_FFPROBE = """#!/bin/sh
if [ "$1" = "-version" ]; then
    printf 'ffprobe version fake\\n'
    exit 0
fi
printf '%s\\n' '{"streams":[{"sample_rate":"22050","channels":1,"codec_name":"pcm_s16be"}]}'
exit 0
"""


def write_executable(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
    return path


def write_fake_say(directory: Path) -> Path:
    return write_executable(directory / "say", FAKE_SAY)


def write_fake_ffmpeg(directory: Path) -> Path:
    return write_executable(directory / "ffmpeg", FAKE_FFMPEG)


def write_fake_ffprobe(directory: Path) -> Path:
    return write_executable(directory / "ffprobe", FAKE_FFPROBE)
