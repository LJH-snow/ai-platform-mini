"""Shared fake adapters for PDF report workflow tests."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from app.rag.pdf_extractor import ExtractedPdf
from app.rag.service import RAGReference
from app.workflows.pdf_report import (
    ReportCompletion,
    RetrievedContext,
)


class FakePdfExtractor:
    def __init__(self, extracted: ExtractedPdf | None = None) -> None:
        self._extracted = extracted or ExtractedPdf(
            filename="sample.pdf",
            text="PDF body text",
            page_count=2,
        )
        self.paths: list[Path] = []

    async def extract(self, path: Path) -> ExtractedPdf:
        self.paths.append(path)
        return self._extracted


class FakeRetriever:
    def __init__(
        self,
        references: Sequence[RAGReference] = (),
        warning: str | None = None,
    ) -> None:
        self._references = tuple(references)
        self._warning = warning
        self.queries: list[str] = []
        self.owner_key_hashes: list[str] = []

    async def retrieve(self, query: str, *, owner_key_hash: str) -> RetrievedContext:
        self.queries.append(query)
        self.owner_key_hashes.append(owner_key_hash)
        return RetrievedContext(
            query=query,
            references=self._references,
            warning=self._warning,
        )


class FakeModel:
    def __init__(
        self,
        content: str = "Fake analysis",
        error: Exception | None = None,
    ) -> None:
        self._content = content
        self._error = error
        self.messages: list[list[tuple[str, str]]] = []
        self.models: list[str | None] = []

    async def complete(
        self,
        messages: Sequence[tuple[str, str]],
        *,
        model: str | None = None,
    ) -> ReportCompletion:
        self.messages.append(list(messages))
        self.models.append(model)
        if self._error is not None:
            raise self._error
        return ReportCompletion(
            content=self._content,
            model=model or "fake-model",
            prompt_tokens=11,
            completion_tokens=7,
        )


def make_reference(
    *,
    document_id: str = "doc-1",
    chunk_id: str = "chunk-1",
    content: str = "Retrieved context",
) -> RAGReference:
    return RAGReference(
        document_id=document_id,
        chunk_id=chunk_id,
        chunk_index=0,
        content=content,
        distance=0.1,
    )
