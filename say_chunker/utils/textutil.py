"""Text normalization, decoding, hashing, and small formatting helpers."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from say_chunker.domain.errors import OutputNotWritableError

_WHITESPACE_RE = re.compile(r"\s+")
_BLANK_LINE_RE = re.compile(r"\n[ \t]*\n+")
_NULL_RE = re.compile(r"\x00")
_TITLE_INVALID_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_DURATION_RE = re.compile(r"\s*(\d+(?:\.\d+)?)\s*([smhdw])\s*", re.IGNORECASE)
_DURATION_UNITS = {
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
    "d": 86400.0,
    "w": 604800.0,
}
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def normalize_paragraph(text: str) -> str:
    """Collapse all whitespace (including internal newlines) to single spaces."""
    return _WHITESPACE_RE.sub(" ", text).strip()


def split_paragraphs(text: str) -> list[str]:
    """Split on blank lines and return normalized, non-empty paragraphs."""
    normalized = normalize_newlines(text)
    raw = _BLANK_LINE_RE.split(normalized)
    paragraphs: list[str] = []
    for part in raw:
        paragraph = normalize_paragraph(part)
        if paragraph:
            paragraphs.append(paragraph)
    return paragraphs


def normalize_document_text(text: str) -> str:
    """Normalize line endings, remove NULs, and rejoin paragraphs with blank lines."""
    cleaned = _NULL_RE.sub("", normalize_newlines(text))
    return "\n\n".join(split_paragraphs(cleaned))


def decode_text(data: bytes, *, source: str) -> tuple[str, list[str]]:
    """Decode text bytes with a sensible fallback chain.

    Returns ``(text, warnings)``. The warning names the chosen encoding when it
    is not UTF-8 so users can fix mojibake at the source.
    """
    warnings: list[str] = []
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if encoding not in {"utf-8-sig", "utf-8"}:
            warnings.append(
                f"{source} is not valid UTF-8; decoded as {encoding}. "
                "Re-save as UTF-8 if characters look wrong."
            )
        return text, warnings
    # latin-1 cannot fail, so this is unreachable in practice.
    raise UnicodeDecodeError("utf-8", data, 0, len(data), "unable to decode text")


def sanitize_filename(title: str, *, fallback: str = "audiobook") -> str:
    """Turn a document title into a safe single-component filename stem."""
    cleaned = _TITLE_INVALID_RE.sub(" ", title)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip().strip(". ")
    if not cleaned:
        cleaned = fallback
    return cleaned[:120].rstrip(". ")


def strip_html_tags(text: str) -> str:
    return _HTML_TAG_RE.sub("", text)


def parse_duration(value: str) -> float:
    """Parse ``2h``, ``90m``, ``1h30m``, ``7d`` or bare seconds.

    Raises:
        ValueError: if the value cannot be parsed.
    """
    text = value.strip().lower()
    if not text:
        raise ValueError("duration is empty")

    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return float(text)

    total = 0.0
    position = 0
    for match in _DURATION_RE.finditer(text):
        if match.start() != position:
            raise ValueError(f"invalid duration {value!r}")
        amount = float(match.group(1))
        unit = match.group(2).lower()
        total += amount * _DURATION_UNITS[unit]
        position = match.end()

    if position != len(text) or position == 0:
        raise ValueError(f"invalid duration {value!r} (use e.g. 2h, 90m, 7d)")

    return total


def format_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0 or seconds != seconds:  # NaN check
        return "unknown"
    total = round(seconds)
    if total < 60:
        return f"{total}s"
    minutes, sec = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {sec:02d}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours:02d}h"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def ensure_directory_writable(directory: Path) -> None:
    """Fail if ``directory`` cannot be created or probed for writing."""
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OutputNotWritableError(f"Cannot create output directory {directory}: {exc}") from exc
    try:
        with tempfile.NamedTemporaryFile(
            dir=directory, prefix=".tts-write-test-", delete=True
        ) as probe:
            probe.write(b"ok")
    except OSError as exc:
        raise OutputNotWritableError(
            f"Cannot write to output directory {directory}: {exc}"
        ) from exc


def ensure_writable_output(path: Path) -> None:
    """Fail fast if ``path``'s parent cannot be written or ``path`` is invalid."""
    if path.exists() and path.is_dir():
        raise OutputNotWritableError(f"Output path is a directory: {path}")
    ensure_directory_writable(path.parent)
    if path.exists() and not os.access(path, os.W_OK):
        raise OutputNotWritableError(f"Output file is not writable: {path}")
