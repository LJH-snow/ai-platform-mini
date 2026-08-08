"""PDF parser — thin wrapper over the existing extractor (zero duplication)."""

from __future__ import annotations

from app.rag.parsers.base import ParsedDocument, SourceFormat
from app.rag.pdf_extractor import extract_pdf_text


class PdfParser:
    """Route PDF bytes through ``extract_pdf_text`` unchanged.

    All pypdf logic, validation, and error types stay in
    ``app/rag/pdf_extractor.py``; this adapter only maps the extracted
    result onto the parser contract.
    """

    def parse(self, filename: str, content: bytes) -> ParsedDocument:
        extracted = extract_pdf_text(
            content,
            filename=filename,
            max_pages=_MAX_PAGES,
            max_text_characters=_MAX_TEXT_CHARACTERS,
        )
        return ParsedDocument(
            filename=extracted.filename,
            text=extracted.text,
            source_format=_SOURCE_FORMAT,
            page_count=extracted.page_count,
        )


# Bounded parsing: PDF extraction must never consume unbounded resources.
_MAX_PAGES = 100
_MAX_TEXT_CHARACTERS = 1_000_000
_SOURCE_FORMAT: SourceFormat = "pdf"
