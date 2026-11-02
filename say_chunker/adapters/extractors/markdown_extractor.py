"""Markdown extractor: strips syntax, preserves headings as hard sections."""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any

from markdown_it import MarkdownIt

from say_chunker.domain.errors import ExtractionError
from say_chunker.domain.models import Section, SourceDocument
from say_chunker.utils.textutil import (
    decode_text,
    normalize_newlines,
    normalize_paragraph,
    strip_html_tags,
)

_FRONT_MATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)


class MarkdownExtractor:
    name = "markdown"

    def extract(self, path: Path) -> SourceDocument:
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ExtractionError(f"Could not read Markdown file {path}: {exc}") from exc

        raw, warnings = decode_text(data, source=str(path))
        raw = normalize_newlines(raw)
        raw = _strip_front_matter(raw)
        tokens = MarkdownIt("commonmark", {"html": True}).parse(raw)
        sections, first_h1 = _tokens_to_sections(tokens)

        title = first_h1 or path.stem or "audiobook"
        full_text = "\n\n".join(section.text for section in sections if section.text)
        return SourceDocument(
            title=title,
            text=full_text,
            page_count=0,
            sections=sections,
            warnings=warnings,
        )


def _strip_front_matter(text: str) -> str:
    match = _FRONT_MATTER_RE.match(text)
    if match is None:
        return text
    return text[match.end() :]


def _tokens_to_sections(tokens: list[Any]) -> tuple[list[Section], str | None]:
    sections: list[Section] = []
    blocks: list[str] = []
    current_title: str | None = None
    current_level = 0
    first_h1: str | None = None

    def flush() -> None:
        nonlocal blocks
        text = "\n\n".join(block for block in blocks if block.strip())
        if text.strip():
            sections.append(Section(title=current_title, text=text, level=current_level))
        blocks = []

    index = 0
    while index < len(tokens):
        token = tokens[index]
        token_type = str(token.type)

        if token_type == "heading_open":
            heading_text = ""
            if index + 1 < len(tokens) and str(tokens[index + 1].type) == "inline":
                heading_text = _inline_text(tokens[index + 1])
            level = _heading_level(str(token.tag))
            flush()
            current_title = heading_text or None
            current_level = level
            if level == 1 and first_h1 is None and heading_text:
                first_h1 = heading_text
            if heading_text:
                blocks.append(heading_text)
            index += 3  # heading_open, inline, heading_close
            continue

        if token_type == "inline":
            text = _inline_text(token)
            if text:
                blocks.append(text)
        elif token_type in {"fence", "code_block"}:
            text = normalize_paragraph(str(token.content))
            if text:
                blocks.append(text)
        elif token_type == "html_block":
            text = normalize_paragraph(html.unescape(strip_html_tags(str(token.content))))
            if text:
                blocks.append(text)

        index += 1

    flush()
    return sections, first_h1


def _inline_text(token: Any) -> str:
    parts: list[str] = []
    children = token.children or []
    for child in children:
        child_type = str(child.type)
        if child_type in {"text", "code_inline"}:
            parts.append(str(child.content))
        elif child_type in {"softbreak", "hardbreak"}:
            parts.append(" ")
        elif child_type == "image":
            alt = str(child.content or "")
            if alt:
                parts.append(alt)
        elif child_type == "html_inline":
            parts.append(html.unescape(strip_html_tags(str(child.content))))
        elif child_type in {"math_inline", "emoji"}:
            parts.append(str(child.content))
    return normalize_paragraph("".join(parts))


def _heading_level(tag: str) -> int:
    if len(tag) >= 2 and tag[0].lower() == "h" and tag[1].isdigit():
        return int(tag[1])
    return 0
