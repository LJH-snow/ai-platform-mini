"""Application service for PDF-to-vector knowledge-base ingestion."""

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime

from app.exceptions.base import ConflictError, ProviderError
from app.rag.chunker import chunk_text
from app.rag.embedder import Embedder
from app.rag.pdf_extractor import extract_pdf_text
from app.rag.vector_store import (
    DocumentPreview,
    DocumentSummary,
    VectorStore,
    validate_owner_key_hash,
)


@dataclass(frozen=True)
class IngestedDocument:
    """Safe response data for one successfully indexed document."""

    document_id: str
    filename: str
    text_characters: int
    chunk_count: int
    content_sha256: str
    embedding_model: str
    created_at: datetime | None


class RAGIngestionService:
    """Coordinate bounded PDF extraction, embedding, and vector persistence."""

    def __init__(
        self,
        embedder: Embedder,
        vector_store: VectorStore,
        *,
        embedding_model: str,
        embedding_dimensions: int,
        chunk_size: int,
        chunk_overlap: int,
        max_pages: int,
        max_text_characters: int,
    ) -> None:
        self._embedder = embedder
        self._vector_store = vector_store
        self._embedding_model = embedding_model
        self._embedding_dimensions = embedding_dimensions
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._max_pages = max_pages
        self._max_text_characters = max_text_characters

    async def ingest_pdf(
        self,
        content: bytes,
        *,
        filename: str | None,
        owner_key_hash: str,
    ) -> IngestedDocument:
        owner_hash = validate_owner_key_hash(owner_key_hash)
        extracted = extract_pdf_text(
            content,
            filename=filename,
            max_pages=self._max_pages,
            max_text_characters=self._max_text_characters,
        )
        chunks = chunk_text(
            extracted.text,
            chunk_size=self._chunk_size,
            overlap=self._chunk_overlap,
        )
        if not chunks:
            raise ProviderError("PDF 没有可用于向量化的文本分块。")

        existing_documents = await self._vector_store.list_documents(
            owner_key_hash=owner_hash
        )
        if any(
            document.filename == extracted.filename for document in existing_documents
        ):
            raise ConflictError("同名文档已存在，请更换文件名后再上传。")

        embeddings = await self._embedder.embed(chunks)
        self._validate_embeddings(embeddings, expected_count=len(chunks))

        content_sha256 = hashlib.sha256(extracted.text.encode("utf-8")).hexdigest()
        document_id = await self._vector_store.add_document(
            source_path=extracted.filename,
            content_sha256=content_sha256,
            embedding_model=self._embedding_model,
            embedding_dimensions=self._embedding_dimensions,
            chunks=chunks,
            embeddings=embeddings,
            owner_key_hash=owner_hash,
        )
        summary = await self._vector_store.get_document_summary(
            document_id, owner_key_hash=owner_hash
        )
        return IngestedDocument(
            document_id=document_id,
            filename=extracted.filename,
            text_characters=len(extracted.text),
            chunk_count=len(chunks),
            content_sha256=content_sha256,
            embedding_model=self._embedding_model,
            created_at=summary.created_at if summary is not None else None,
        )

    def _validate_embeddings(
        self, embeddings: list[list[float]], *, expected_count: int
    ) -> None:
        if len(embeddings) != expected_count:
            raise ProviderError("Embedding 服务返回的分块数量不一致。")
        if not embeddings or any(
            len(embedding) != self._embedding_dimensions
            or not all(math.isfinite(value) for value in embedding)
            for embedding in embeddings
        ):
            raise ProviderError("Embedding 服务返回的向量维度或数值不合法。")

    async def list_documents(self, *, owner_key_hash: str) -> list[DocumentSummary]:
        """Return safe metadata for one API-key tenant."""

        owner_hash = validate_owner_key_hash(owner_key_hash)
        return await self._vector_store.list_documents(owner_key_hash=owner_hash)

    async def delete_document(self, *, owner_key_hash: str, document_id: str) -> bool:
        """Delete one document only inside the authenticated tenant."""

        owner_hash = validate_owner_key_hash(owner_key_hash)
        return await self._vector_store.delete_document(owner_hash, document_id)

    async def get_document_preview(
        self, *, owner_key_hash: str, document_id: str
    ) -> DocumentPreview | None:
        """Return bounded extracted text for one authenticated document."""

        owner_hash = validate_owner_key_hash(owner_key_hash)
        return await self._vector_store.get_document_preview(owner_hash, document_id)
