"""Markdown parser — strips YAML frontmatter, keeps everything else."""

from __future__ import annotations

from app.rag.parsers.base import ParsedDocument, SourceFormat
from app.rag.parsers.text import decode_text


class MarkdownParser:
    """Decode markdown text and strip a leading YAML frontmatter block.

    Frontmatter is only recognized when the file starts with ``---`` and
    a second ``---`` line follows; anything else is preserved verbatim
    (code blocks stay — content judgement belongs to the safety layer).
    """

    def parse(self, filename: str, content: bytes) -> ParsedDocument:
        text = _strip_frontmatter(decode_text(content))
        return ParsedDocument(
            filename=filename,
            text=text,
            source_format=_SOURCE_FORMAT,
            page_count=None,
        )


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---\n") and text.strip() != "---":
        return text
    lines = text.splitlines()
    closing = next(
        (
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        ),
        None,
    )
    if closing is None:
        # Unterminated frontmatter opener: keep the whole file verbatim.
        return text
    return "\n".join(lines[closing + 1 :]).lstrip("\n")


_SOURCE_FORMAT: SourceFormat = "markdown"
