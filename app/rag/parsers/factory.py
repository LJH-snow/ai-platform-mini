"""Parser factory — route documents by filename extension."""

from __future__ import annotations

from pathlib import PurePath

from app.exceptions.base import RAGDocumentValidationError
from app.rag.parsers.base import ParsedDocument, Parser
from app.rag.parsers.docx import DocxParser
from app.rag.parsers.html import HtmlParser
from app.rag.parsers.markdown import MarkdownParser
from app.rag.parsers.pdf import PdfParser
from app.rag.parsers.text import TextParser
from app.rag.parsers.xlsx import XlsxParser

# Lower-case extension → parser instance (stateless).
_PARSERS: dict[str, Parser] = {
    ".pdf": PdfParser(),
    ".txt": TextParser(),
    ".md": MarkdownParser(),
    ".markdown": MarkdownParser(),
    ".docx": DocxParser(),
    ".xlsx": XlsxParser(),
    ".html": HtmlParser(),
    ".htm": HtmlParser(),
}

_SUPPORTED_EXTENSIONS = ", ".join(sorted({ext.lstrip(".") for ext in _PARSERS}))


def create_parser(filename: str | None) -> Parser:
    """Return the parser for a filename, rejecting unknown formats.

    The filename is reduced to its basename first so directory components
    can never influence routing.
    """
    safe_name = PurePath((filename or "").replace("\\", "/")).name.strip()
    if not safe_name:
        raise RAGDocumentValidationError("文件名不能为空。")
    extension = PurePath(safe_name).suffix.lower()
    parser = _PARSERS.get(extension)
    if parser is None:
        raise RAGDocumentValidationError(
            f"不支持的文档格式：{extension or '(无扩展名)'}。"
            f"支持：{_SUPPORTED_EXTENSIONS}。"
        )
    return parser


def parse_document(filename: str | None, content: bytes) -> ParsedDocument:
    """Route and parse one document; the ingestion entry point."""
    parser = create_parser(filename)
    return parser.parse(safe_basename(filename), content)


def safe_basename(filename: str | None) -> str:
    """Return a display/storage-safe basename for the parsed document."""
    name = PurePath((filename or "document").replace("\\", "/")).name.strip()
    return name or "document"
