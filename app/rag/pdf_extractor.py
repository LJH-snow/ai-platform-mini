"""Bounded text extraction for uploaded PDF documents."""

import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePath

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.exceptions.base import RAGDocumentValidationError

_PDF_SIGNATURE = b"%PDF-"
_MAX_FILENAME_LENGTH = 255
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]+")


@dataclass(frozen=True)
class ExtractedPdf:
    """Validated PDF metadata and plain text extracted from its pages."""

    filename: str
    text: str
    page_count: int


def normalize_pdf_filename(filename: str | None) -> str:
    """Return a safe display/storage name without directory components."""

    raw_name = PurePath((filename or "document.pdf").replace("\\", "/")).name.strip()
    raw_name = _CONTROL_CHARACTERS.sub(" ", raw_name).strip()
    if not raw_name:
        raw_name = "document.pdf"
    if not raw_name.lower().endswith(".pdf"):
        raw_name = f"{raw_name}.pdf"
    return raw_name[:_MAX_FILENAME_LENGTH]


def extract_pdf_text(
    content: bytes,
    *,
    filename: str | None,
    max_pages: int,
    max_text_characters: int,
) -> ExtractedPdf:
    """Extract bounded plain text from a PDF byte payload.

    The parser never returns embedded files, links, annotations, or PDF
    instructions. Only page text is passed to the RAG ingestion pipeline.
    """

    if not content.startswith(_PDF_SIGNATURE):
        raise RAGDocumentValidationError("上传文件不是有效的 PDF。")

    try:
        reader = PdfReader(BytesIO(content), strict=False)
        if reader.is_encrypted:
            raise RAGDocumentValidationError("暂不支持加密 PDF，请上传未加密文件。")
        page_count = len(reader.pages)
        if page_count == 0:
            raise RAGDocumentValidationError("PDF 不包含可读取的页面。")
        if page_count > max_pages:
            raise RAGDocumentValidationError(
                f"PDF 页数超过限制（最多 {max_pages} 页）。"
            )

        page_texts: list[str] = []
        total_characters = 0
        for page in reader.pages:
            page_text = (page.extract_text() or "").strip()
            if not page_text:
                continue
            page_texts.append(page_text)
            total_characters += len(page_text)
            if total_characters > max_text_characters:
                raise RAGDocumentValidationError(
                    f"PDF 提取文本超过限制（最多 {max_text_characters} 个字符）。"
                )
    except RAGDocumentValidationError:
        raise
    except (PdfReadError, ValueError, TypeError) as exc:
        raise RAGDocumentValidationError("PDF 无法解析，请确认文件内容完整。") from exc
    except Exception as exc:
        raise RAGDocumentValidationError("PDF 解析失败，请更换文件后重试。") from exc

    text = "\n\n".join(page_texts).strip()
    if not text:
        raise RAGDocumentValidationError("PDF 没有可提取的文本内容。")

    return ExtractedPdf(
        filename=normalize_pdf_filename(filename),
        text=text,
        page_count=page_count,
    )
