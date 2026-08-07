from datetime import UTC, datetime

import pytest

from app.exceptions.base import ConflictError, ProviderError
from app.rag.ingestion import RAGIngestionService
from app.rag.pdf_extractor import ExtractedPdf
from app.rag.vector_store import DocumentSummary, SearchResult


class FakeEmbedder:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(index), 1.0] for index, _ in enumerate(texts)]

    async def embed_query(self, text: str) -> list[float]:
        return [1.0, 1.0]

    async def close(self) -> None:
        return None


class FakeVectorStore:
    def __init__(self, documents: list[DocumentSummary] | None = None) -> None:
        self.added: dict[str, object] = {}
        self.documents = documents or []

    async def add_document(
        self,
        source_path: str,
        content_sha256: str,
        embedding_model: str,
        embedding_dimensions: int,
        chunks: list[str],
        embeddings: list[list[float]],
        *,
        owner_key_hash: str | None = None,
    ) -> str:
        self.added = {
            "source_path": source_path,
            "content_sha256": content_sha256,
            "embedding_model": embedding_model,
            "embedding_dimensions": embedding_dimensions,
            "chunks": chunks,
            "embeddings": embeddings,
        }
        return "123e4567-e89b-12d3-a456-426614174000"

    async def get_document_summary(
        self, document_id: str, *, owner_key_hash: str | None = None
    ) -> DocumentSummary:
        return DocumentSummary(
            document_id=document_id,
            filename="brief.pdf",
            content_sha256=str(self.added["content_sha256"]),
            embedding_model="test-embed",
            embedding_dimensions=2,
            created_at=datetime.now(UTC),
            chunk_count=1,
            text_characters=12,
        )

    async def list_documents(
        self, *, owner_key_hash: str | None = None
    ) -> list[DocumentSummary]:
        return self.documents

    async def search(
        self,
        query_embedding: list[float],
        top_k: int,
        *,
        owner_key_hash: str | None = None,
        query: str | None = None,
    ) -> list[SearchResult]:
        del query
        return []

    async def delete_document(self, owner_key_hash: str, document_id: str) -> bool:
        return True

    async def get_document_preview(
        self,
        owner_key_hash: str,
        document_id: str,
        *,
        max_characters: int = 4000,
    ) -> None:
        return None


@pytest.mark.asyncio
async def test_ingest_pdf_extracts_embeds_and_persists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.rag.ingestion.extract_pdf_text",
        lambda *args, **kwargs: ExtractedPdf("brief.pdf", "hello knowledge", 1),
    )
    store = FakeVectorStore()
    service = RAGIngestionService(
        FakeEmbedder(),
        store,
        embedding_model="test-embed",
        embedding_dimensions=2,
        chunk_size=100,
        chunk_overlap=10,
        max_pages=10,
        max_text_characters=1000,
    )

    result = await service.ingest_pdf(
        b"%PDF-fake", filename="brief.pdf", owner_key_hash="a" * 64
    )

    assert result.document_id == "123e4567-e89b-12d3-a456-426614174000"
    assert result.filename == "brief.pdf"
    assert result.chunk_count == 1
    assert store.added["embedding_dimensions"] == 2
    assert store.added["chunks"] == ["hello knowledge"]


@pytest.mark.asyncio
async def test_ingest_pdf_rejects_empty_chunk_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.rag.ingestion.extract_pdf_text",
        lambda *args, **kwargs: ExtractedPdf("empty.pdf", "", 1),
    )
    service = RAGIngestionService(
        FakeEmbedder(),
        FakeVectorStore(),
        embedding_model="test-embed",
        embedding_dimensions=2,
        chunk_size=100,
        chunk_overlap=10,
        max_pages=10,
        max_text_characters=1000,
    )

    with pytest.raises(ProviderError, match="分块"):
        await service.ingest_pdf(
            b"%PDF-fake", filename="empty.pdf", owner_key_hash="a" * 64
        )


@pytest.mark.asyncio
async def test_ingest_pdf_rejects_same_filename(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.rag.ingestion.extract_pdf_text",
        lambda *args, **kwargs: ExtractedPdf("brief.pdf", "new content", 1),
    )
    existing = DocumentSummary(
        document_id="doc-old",
        filename="brief.pdf",
        content_sha256="b" * 64,
        embedding_model="test-embed",
        embedding_dimensions=2,
        created_at=None,
        chunk_count=1,
        text_characters=12,
    )
    service = RAGIngestionService(
        FakeEmbedder(),
        FakeVectorStore([existing]),
        embedding_model="test-embed",
        embedding_dimensions=2,
        chunk_size=100,
        chunk_overlap=10,
        max_pages=10,
        max_text_characters=1000,
    )

    with pytest.raises(ConflictError, match="同名文档"):
        await service.ingest_pdf(
            b"%PDF-fake", filename="brief.pdf", owner_key_hash="a" * 64
        )
