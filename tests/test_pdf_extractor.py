from io import BytesIO
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.exceptions.base import RAGDocumentValidationError
from app.rag import pdf_extractor
from app.rag.pdf_extractor import extract_pdf_text, normalize_pdf_filename


def test_normalize_pdf_filename_strips_path_and_adds_suffix() -> None:
    assert normalize_pdf_filename("../../notes/project") == "project.pdf"
    assert normalize_pdf_filename(r"..\secret\brief.pdf") == "brief.pdf"
    assert normalize_pdf_filename("notes\n.pdf") == "notes .pdf"
    assert normalize_pdf_filename(None) == "document.pdf"


def test_extract_pdf_text_returns_page_text(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakePage:
        def __init__(self, text: str) -> None:
            self._text = text

        def extract_text(self) -> str:
            return self._text

    class FakeReader:
        is_encrypted = False
        pages = [FakePage("第一段"), FakePage("第二段")]

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

    monkeypatch.setattr(pdf_extractor, "PdfReader", FakeReader)

    result = extract_pdf_text(
        b"%PDF-fake",
        filename="/tmp/brief.pdf",
        max_pages=10,
        max_text_characters=100,
    )

    assert result.filename == "brief.pdf"
    assert result.page_count == 2
    assert result.text == "第一段\n\n第二段"


def test_extract_pdf_text_uses_real_pypdf_parser() -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 72 200 Td (Hello PDF) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    payload = BytesIO()
    writer.write(payload)

    result = extract_pdf_text(
        payload.getvalue(),
        filename="brief.pdf",
        max_pages=10,
        max_text_characters=100,
    )

    assert result.text == "Hello PDF"


def test_extract_pdf_text_rejects_invalid_signature() -> None:
    with pytest.raises(RAGDocumentValidationError, match="有效的 PDF"):
        extract_pdf_text(
            b"not-a-pdf",
            filename="notes.pdf",
            max_pages=10,
            max_text_characters=100,
        )


def test_extract_pdf_text_rejects_page_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeReader:
        is_encrypted = False
        pages = [SimpleNamespace(extract_text=lambda: "text") for _ in range(2)]

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

    monkeypatch.setattr(pdf_extractor, "PdfReader", FakeReader)

    with pytest.raises(RAGDocumentValidationError, match="页数超过限制"):
        extract_pdf_text(
            b"%PDF-fake",
            filename="notes.pdf",
            max_pages=1,
            max_text_characters=100,
        )
