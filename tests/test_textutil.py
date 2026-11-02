from __future__ import annotations

from pathlib import Path

import pytest

from say_chunker.domain.errors import OutputNotWritableError
from say_chunker.utils.textutil import (
    decode_text,
    ensure_writable_output,
    format_duration,
    normalize_document_text,
    parse_duration,
    sanitize_filename,
    sha256_bytes,
    sha256_file,
    split_paragraphs,
)


def test_parse_duration_units() -> None:
    assert parse_duration("2h") == 7200
    assert parse_duration("90m") == 5400
    assert parse_duration("1h30m") == 5400
    assert parse_duration("7d") == 604800
    assert parse_duration("45") == 45
    assert parse_duration("1.5h") == 5400


@pytest.mark.parametrize("value", ["", "nope", "2x", "1h nope", "-5m"])
def test_parse_duration_invalid(value: str) -> None:
    with pytest.raises(ValueError):
        parse_duration(value)


def test_format_duration() -> None:
    assert format_duration(None) == "unknown"
    assert format_duration(12) == "12s"
    assert format_duration(65) == "1m 05s"
    assert format_duration(3660) == "1h 01m"
    assert format_duration(90000) == "1d 01h"


def test_split_paragraphs_and_normalize() -> None:
    text = "First line\ncontinues here.\n\nSecond paragraph.\n\n\nThird."
    assert split_paragraphs(text) == [
        "First line continues here.",
        "Second paragraph.",
        "Third.",
    ]
    assert normalize_document_text(text) == (
        "First line continues here.\n\nSecond paragraph.\n\nThird."
    )


def test_sanitize_filename() -> None:
    assert sanitize_filename('A/B: "Book"?') == "A B Book"
    assert sanitize_filename("   ...   ") == "audiobook"
    assert len(sanitize_filename("x" * 500)) == 120
    assert sanitize_filename("", fallback="untitled") == "untitled"


def test_decode_text_utf8_and_fallback() -> None:
    text, warnings = decode_text("Café".encode(), source="x.txt")
    assert text == "Café"
    assert warnings == []

    text, warnings = decode_text(b"Caf\xe9", source="x.txt")
    assert text == "Café"
    assert warnings and "cp1252" in warnings[0]


def test_sha256(tmp_path: Path) -> None:
    path = tmp_path / "data.bin"
    path.write_bytes(b"hello")
    assert sha256_bytes(b"hello") == sha256_file(path)
    assert len(sha256_file(path)) == 64


def test_ensure_writable_output(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "out.mp3"
    ensure_writable_output(nested)
    assert nested.parent.is_dir()


def test_ensure_writable_output_rejects_file_parent(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    with pytest.raises(OutputNotWritableError):
        ensure_writable_output(blocker / "out.mp3")
