"""Integration tests for PgVectorStore against a real PostgreSQL/pgvector database.

These tests require:
  - ``testcontainers`` package installed
  - Docker running locally
  - ``INTEGRATION_TEST=1`` environment variable

Run with:
    INTEGRATION_TEST=1 pytest tests/test_pg_vector_store_integration.py -v
"""

import os
from collections.abc import AsyncGenerator

import pytest

from app.db.init import dispose_db, init_db
from app.db.session import create_async_session_factory
from app.exceptions.base import ConflictError
from app.rag.pg_vector_store import PgVectorStore

_SKIP_REASON = "Set INTEGRATION_TEST=1 to run pgvector integration tests"
_OWNER_KEY_HASH = "a" * 64

pytestmark = pytest.mark.skipif(
    not os.getenv("INTEGRATION_TEST"),
    reason=_SKIP_REASON,
)


@pytest.fixture()
async def vector_store() -> AsyncGenerator[PgVectorStore, None]:
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        database_url = pg.get_connection_url().replace("psycopg2", "asyncpg")
        await init_db(database_url, include_rag=True)
        factory = create_async_session_factory()
        store = PgVectorStore(session_factory=factory)
        yield store
        await dispose_db()


def _make_embedding(dim: int = 768, value: float = 0.01) -> list[float]:
    """Create a simple embedding vector for testing."""
    return [value] * dim


class TestPgVectorStoreIntegration:
    @pytest.mark.asyncio
    async def test_add_and_search_document(self, vector_store: PgVectorStore) -> None:
        """End-to-end: ingest a document and retrieve it via cosine search."""
        doc_id = await vector_store.add_document(
            source_path="test.txt",
            content_sha256="abc123",
            embedding_model="nomic-embed-text",
            embedding_dimensions=768,
            chunks=["hello world"],
            embeddings=[_make_embedding()],
            owner_key_hash=_OWNER_KEY_HASH,
        )
        assert doc_id is not None

        # Search with a similar embedding
        results = await vector_store.search(
            query_embedding=_make_embedding(), top_k=5, owner_key_hash=_OWNER_KEY_HASH
        )
        assert len(results) == 1
        assert results[0].document_id == doc_id
        assert results[0].content == "hello world"
        assert results[0].chunk_index == 0

    @pytest.mark.asyncio
    async def test_cosine_distance_ordering(self, vector_store: PgVectorStore) -> None:
        """Cosine distance should rank more similar vectors first."""
        # Create vectors with different directions (not just scaled):
        # - doc_close: first half is 1.0, second half is 0.0
        # - doc_far: first half is 0.0, second half is 1.0
        # Query matches doc_close direction → should rank first.
        half = 384  # half of 768 dimensions
        close_vec = [1.0] * half + [0.0] * half
        far_vec = [0.0] * half + [1.0] * half
        query_vec = [0.9] * half + [0.1] * half  # similar to close_vec

        await vector_store.add_document(
            source_path="doc_close.txt",
            content_sha256="sha_close",
            embedding_model="nomic-embed-text",
            embedding_dimensions=768,
            chunks=["close content"],
            embeddings=[close_vec],
            owner_key_hash=_OWNER_KEY_HASH,
        )
        await vector_store.add_document(
            source_path="doc_far.txt",
            content_sha256="sha_far",
            embedding_model="nomic-embed-text",
            embedding_dimensions=768,
            chunks=["far content"],
            embeddings=[far_vec],
            owner_key_hash=_OWNER_KEY_HASH,
        )

        results = await vector_store.search(
            query_embedding=query_vec, top_k=2, owner_key_hash=_OWNER_KEY_HASH
        )
        assert len(results) == 2
        assert results[0].content == "close content"
        assert results[0].distance < results[1].distance

    @pytest.mark.asyncio
    async def test_duplicate_sha256_raises_conflict(
        self, vector_store: PgVectorStore
    ) -> None:
        """Ingesting the same SHA-256 twice should raise ConflictError."""
        await vector_store.add_document(
            source_path="dup.txt",
            content_sha256="dup_sha",
            embedding_model="nomic-embed-text",
            embedding_dimensions=768,
            chunks=["first"],
            embeddings=[_make_embedding()],
            owner_key_hash=_OWNER_KEY_HASH,
        )
        with pytest.raises(ConflictError, match="already exists"):
            await vector_store.add_document(
                source_path="dup.txt",
                content_sha256="dup_sha",
                embedding_model="nomic-embed-text",
                embedding_dimensions=768,
                chunks=["second"],
                embeddings=[_make_embedding()],
                owner_key_hash=_OWNER_KEY_HASH,
            )

    @pytest.mark.asyncio
    async def test_supersede_same_path_different_sha(
        self, vector_store: PgVectorStore
    ) -> None:
        """Same source_path with different SHA-256 should supersede old doc."""
        first_id = await vector_store.add_document(
            source_path="versioned.txt",
            content_sha256="version1_sha",
            embedding_model="nomic-embed-text",
            embedding_dimensions=768,
            chunks=["version 1 content"],
            embeddings=[_make_embedding(value=0.5)],
            owner_key_hash=_OWNER_KEY_HASH,
        )

        second_id = await vector_store.add_document(
            source_path="versioned.txt",
            content_sha256="version2_sha",
            embedding_model="nomic-embed-text",
            embedding_dimensions=768,
            chunks=["version 2 content"],
            embeddings=[_make_embedding(value=0.5)],
            owner_key_hash=_OWNER_KEY_HASH,
        )

        assert first_id != second_id

        # Search should only find the new version
        results = await vector_store.search(
            query_embedding=_make_embedding(value=0.5),
            top_k=10,
            owner_key_hash=_OWNER_KEY_HASH,
        )
        assert len(results) == 1
        assert results[0].content == "version 2 content"
        assert results[0].document_id == second_id

    @pytest.mark.asyncio
    async def test_cascade_delete_on_supersede(
        self, vector_store: PgVectorStore
    ) -> None:
        """When a document is superseded, its chunks should be deleted."""
        await vector_store.add_document(
            source_path="cascade.txt",
            content_sha256="cascade_v1",
            embedding_model="nomic-embed-text",
            embedding_dimensions=768,
            chunks=["chunk_a", "chunk_b"],
            embeddings=[_make_embedding(value=0.3), _make_embedding(value=0.4)],
            owner_key_hash=_OWNER_KEY_HASH,
        )

        await vector_store.add_document(
            source_path="cascade.txt",
            content_sha256="cascade_v2",
            embedding_model="nomic-embed-text",
            embedding_dimensions=768,
            chunks=["chunk_c"],
            embeddings=[_make_embedding(value=0.3)],
            owner_key_hash=_OWNER_KEY_HASH,
        )

        # Search should only find chunk_c, not chunk_a or chunk_b
        results = await vector_store.search(
            query_embedding=_make_embedding(value=0.3),
            top_k=10,
            owner_key_hash=_OWNER_KEY_HASH,
        )
        assert len(results) == 1
        assert results[0].content == "chunk_c"

    @pytest.mark.asyncio
    async def test_source_path_unique_constraint(
        self, vector_store: PgVectorStore
    ) -> None:
        """Concurrent insert of same source_path should hit UNIQUE constraint."""
        await vector_store.add_document(
            source_path="unique_path.txt",
            content_sha256="unique_sha_1",
            embedding_model="nomic-embed-text",
            embedding_dimensions=768,
            chunks=["first"],
            embeddings=[_make_embedding()],
            owner_key_hash=_OWNER_KEY_HASH,
        )

        # Different SHA but same path — should supersede, not conflict
        doc_id = await vector_store.add_document(
            source_path="unique_path.txt",
            content_sha256="unique_sha_2",
            embedding_model="nomic-embed-text",
            embedding_dimensions=768,
            chunks=["second"],
            embeddings=[_make_embedding()],
            owner_key_hash=_OWNER_KEY_HASH,
        )
        assert doc_id is not None

    @pytest.mark.asyncio
    async def test_search_returns_empty_when_no_documents(
        self, vector_store: PgVectorStore
    ) -> None:
        """Search on an empty knowledge base returns empty list."""
        results = await vector_store.search(
            query_embedding=_make_embedding(), top_k=5, owner_key_hash=_OWNER_KEY_HASH
        )
        assert results == []
