"""Document parser contracts — deterministic, synchronous parsing.

Parsers convert raw file bytes into a normalized ``ParsedDocument``;
they never judge content (injection safety is the safety layer's job).
The factory routes by filename extension so ingestion is format-agnostic
and P2 formats (DOCX/XLSX/HTML) plug in with one factory entry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

SourceFormat = Literal["pdf", "txt", "markdown", "docx", "xlsx", "html"]


@dataclass(frozen=True)
class ParsedDocument:
    """Normalized output of one parser."""

    filename: str
    text: str
    source_format: SourceFormat
    page_count: int | None = None


@runtime_checkable
class Parser(Protocol):
    def parse(self, filename: str, content: bytes) -> ParsedDocument: ...
