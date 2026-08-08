"""HTML parser — visible text extraction via the stdlib HTMLParser.

Zero new dependencies: only text nodes are accumulated; script/style
content and comments are skipped; block-level tags introduce newlines.
Entities are decoded by HTMLParser automatically.  Malformed/truncated
HTML never raises (the stdlib parser is tolerant).
"""

from __future__ import annotations

from html.parser import HTMLParser

from app.exceptions.base import RAGDocumentValidationError
from app.rag.parsers.base import ParsedDocument, SourceFormat
from app.rag.parsers.text import decode_text

# Fixed safety ceiling (parser layer): config may only tighten this.
_MAX_TEXT_CHARACTERS = 5_000_000

_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "li",
        "tr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "section",
        "article",
        "br",
        "ul",
        "ol",
        "table",
        "blockquote",
    }
)


class _VisibleTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in ("script", "style"):
            self._skip_depth += 1
            return
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data.strip():
            self._parts.append(data)

    @property
    def text(self) -> str:
        return "\n".join(part.strip() for part in self._parts if part.strip())


class HtmlParser:
    """Extract visible text; never judges content (safety layer's job)."""

    def parse(self, filename: str, content: bytes) -> ParsedDocument:
        text = _extract_visible_text(content)
        if len(text) > _MAX_TEXT_CHARACTERS:
            raise RAGDocumentValidationError("HTML 文档内容超过限制。")
        return ParsedDocument(
            filename=filename,
            text=text,
            source_format=_SOURCE_FORMAT,
            page_count=None,
        )


def _extract_visible_text(content: bytes) -> str:
    try:
        source = decode_text(content)
    except RAGDocumentValidationError:
        # HTML byte content is commonly Latin-1/Windows-1252 without a
        # declared charset; fall back to lossy latin-1 so parsing never
        # fails on encoding alone.
        source = content.decode("latin-1", errors="replace")
    extractor = _VisibleTextExtractor()
    extractor.feed(source)
    extractor.close()
    return extractor.text


_SOURCE_FORMAT: SourceFormat = "html"
