from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class SearchResult:
    document_id: str
    chunk_id: str
    chunk_index: int
    content: str
    distance: float


@runtime_checkable
class VectorStore(Protocol):
    async def add_document(
        self,
        source_path: str,
        content_sha256: str,
        embedding_model: str,
        embedding_dimensions: int,
        chunks: list[str],
        embeddings: list[list[float]],
    ) -> str: ...

    async def search(
        self,
        query_embedding: list[float],
        top_k: int,
    ) -> list[SearchResult]: ...
