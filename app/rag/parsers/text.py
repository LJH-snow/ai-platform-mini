"""Plain-text parser — deterministic byte decoding without sniffing."""

from __future__ import annotations

from app.exceptions.base import RAGDocumentValidationError
from app.rag.parsers.base import ParsedDocument, SourceFormat


class TextParser:
    """Decode bytes with a fixed encoding fallback chain.

    Order is deterministic (utf-8-sig strips a BOM; gb18030 covers common
    Chinese encodings).  No heuristic sniffing: anything that fails both
    is rejected with a clear message.
    """

    def parse(self, filename: str, content: bytes) -> ParsedDocument:
        text = decode_text(content)
        return ParsedDocument(
            filename=filename,
            text=text,
            source_format=_SOURCE_FORMAT,
            page_count=None,
        )


def decode_text(content: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise RAGDocumentValidationError("无法解码文本文件：仅支持 UTF-8 或 GB18030 编码。")


_SOURCE_FORMAT: SourceFormat = "txt"
