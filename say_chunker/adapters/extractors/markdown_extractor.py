"""Markdown extractor: strip syntax, keep headings as section boundaries."""

from __future__ import annotations

from pathlib import Path

from markdown_it import MarkdownIt
from markdown_it.token import Token

from say_chunker.domain.models import Section, SourceDocument
from say_chunker.utils.textutil import decode_text_bytes


class MarkdownExtractor:
    name = "md"

    def __init__(self) -> None:
        self._md = MarkdownIt("commonmark")

    def extract(self, path: Path, *, password: str | None = None) -> SourceDocument:
        del password  # not applicable
        text, warnings = decode_text_bytes(path.read_bytes())
        sections, title = self._build_sections(self._md.parse(text), path.stem)
        return SourceDocument(
            title=title,
            text="\n\n".join(section.text for section in sections),
            page_count=1,
            sections=sections,
            warnings=warnings,
        )

    def _build_sections(
        self, tokens: list[Token], fallback_title: str
    ) -> tuple[list[Section], str]:
        sections: list[Section] = []
        title = fallback_title
        seen_h1 = False

        current_title: str | None = None
        current_level = 0
        buffer: list[str] = []

        def flush() -> None:
            nonlocal buffer
            body = "\n\n".join(segment for segment in buffer if segment.strip())
            buffer = []
            if body.strip():
                sections.append(Section(title=current_title, text=body, level=current_level))

        i = 0
        total = len(tokens)
        while i < total:
            token = tokens[i]
            if token.type == "heading_open":
                flush()
                current_level = int(token.tag[1])
                heading_text = ""
                if i + 1 < total and tokens[i + 1].type == "inline":
                    heading_text = _inline_text(tokens[i + 1]).strip()
                current_title = heading_text or None
                if current_level == 1 and not seen_h1:
                    title = heading_text
                    seen_h1 = True
                while i < total and tokens[i].type != "heading_close":
                    i += 1
                i += 1  # skip heading_close
                continue
            if token.type == "inline":
                content = _inline_text(token).strip()
                if content:
                    buffer.append(content)
            elif token.type in ("fence", "code_block"):
                content = token.content.strip()
                if content:
                    buffer.append(content)
            i += 1

        flush()
        if not sections:
            sections = [Section(title=None, text="", level=0)]
        return sections, title


def _inline_text(token: Token) -> str:
    parts: list[str] = []
    _collect_inline(token, parts)
    return "".join(parts)


def _collect_inline(token: Token, parts: list[str]) -> None:
    if token.type in ("text", "code_inline", "html_inline"):
        parts.append(token.content)
    elif token.type in ("softbreak", "hardbreak"):
        parts.append("\n")
    elif token.type == "image":
        parts.append(token.content)
    else:
        for child in token.children or []:
            _collect_inline(child, parts)
