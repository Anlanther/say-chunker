"""Text decoding helpers."""

from __future__ import annotations


def decode_text_bytes(raw: bytes) -> tuple[str, list[str]]:
    """Decode bytes to text with sensible fallbacks.

    Returns ``(text, warnings)``. Prefers UTF-8, then UTF-8 with BOM, then
    Latin-1 as a lossy last resort (with a warning).
    """
    for encoding in ("utf-8", "utf-8-sig"):
        try:
            return raw.decode(encoding), []
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1"), ["file is not valid UTF-8; decoded as Latin-1"]
