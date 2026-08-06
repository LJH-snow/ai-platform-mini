from unittest.mock import AsyncMock, MagicMock

import pytest

from app.exceptions.base import ConflictError
from app.rag.pg_vector_store import PgVectorStore
from app.rag.vector_store import SearchResult

OWNER_KEY_HASH = "a" * 64


def _make_mock_session() -> AsyncMock:
    session = AsyncMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    return session


def _make_mock_session_factory() -> tuple[MagicMock, AsyncMock]:
    """Return (session_factory, session) for async context manager."""
    session = _make_mock_session()
    context_manager = AsyncMock()
    context_manager.__aenter__ = AsyncMock(return_value=session)
    context_manager.__aexit__ = AsyncMock(return_value=False)
    session_factory = MagicMock(return_value=context_manager)
    return session_factory, session


class TestPgVectorStoreAddDocument:
    @pytest.mark.asyncio
    async def test_add_document_success(self) -> None:
        session_factory, session = _make_mock_session_factory()

        # Mock: no existing model, SHA-256, or path match.
        model_result = MagicMock()
        model_result.all.return_value = []
        scalar_result = MagicMock()
        scalar_result.scalar_one_or_none.return_value = None
        session.execute.side_effect = [
            MagicMock(),
            model_result,
            scalar_result,
            scalar_result,
        ]

        # After flush, the document should have an id assigned
        def _set_doc_id(doc: object) -> None:
            doc.id = "test-doc-id"  # type: ignore[attr-defined]

        session.add.side_effect = _set_doc_id

        store = PgVectorStore(session_factory=session_factory)

        doc_id = await store.add_document(
            source_path="test.txt",
            content_sha256="abc123",
            embedding_model="nomic-embed-text",
            embedding_dimensions=768,
            chunks=["hello world"],
            embeddings=[[0.1, 0.2, 0.3]],
            owner_key_hash=OWNER_KEY_HASH,
        )

        assert doc_id == "test-doc-id"
        session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_document_duplicate_raises_conflict(self) -> None:
        session_factory, session = _make_mock_session_factory()

        # Mock: no existing model, then existing document with same SHA-256.
        existing_doc = MagicMock()
        model_result = MagicMock()
        model_result.all.return_value = []
        scalar_result_sha = MagicMock()
        scalar_result_sha.scalar_one_or_none.return_value = existing_doc
        session.execute.side_effect = [MagicMock(), model_result, scalar_result_sha]

        store = PgVectorStore(session_factory=session_factory)

        with pytest.raises(ConflictError, match="conflicts"):
            await store.add_document(
                source_path="test.txt",
                content_sha256="abc123",
                embedding_model="nomic-embed-text",
                embedding_dimensions=768,
                chunks=["hello"],
                embeddings=[[0.1]],
                owner_key_hash=OWNER_KEY_HASH,
            )

        session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_add_document_rejects_same_path_for_same_owner(self) -> None:
        """A same-owner filename cannot silently replace an existing document."""
        session_factory, session = _make_mock_session_factory()

        model_result = MagicMock()
        model_result.all.return_value = []
        # SHA-256 check (no match), path check (match).
        sha_result = MagicMock()
        sha_result.scalar_one_or_none.return_value = None
        path_result = MagicMock()
        path_result.scalar_one_or_none.return_value = "old-doc-id"
        session.execute.side_effect = [
            MagicMock(),
            model_result,
            sha_result,
            path_result,
        ]

        store = PgVectorStore(session_factory=session_factory)

        with pytest.raises(ConflictError, match="conflicts"):
            await store.add_document(
                source_path="test.txt",
                content_sha256="new_hash",
                embedding_model="nomic-embed-text",
                embedding_dimensions=768,
                chunks=["updated content"],
                embeddings=[[0.4, 0.5, 0.6]],
                owner_key_hash=OWNER_KEY_HASH,
            )

        session.commit.assert_not_awaited()


class TestPgVectorStoreSearch:
    @pytest.mark.asyncio
    async def test_search_returns_results(self) -> None:
        session_factory, session = _make_mock_session_factory()

        mock_row = MagicMock()
        mock_row.document_id = "doc-1"
        mock_row.id = "chunk-1"
        mock_row.chunk_index = 0
        mock_row.content = "Some text"
        mock_row.distance = 0.15

        result_proxy = MagicMock()
        result_proxy.all.return_value = [mock_row]
        session.execute.return_value = result_proxy

        store = PgVectorStore(session_factory=session_factory)
        results = await store.search(
            query_embedding=[0.1, 0.2, 0.3], top_k=5, owner_key_hash="a" * 64
        )

        assert len(results) == 1
        assert isinstance(results[0], SearchResult)
        assert results[0].document_id == "doc-1"
        assert results[0].chunk_id == "chunk-1"
        assert results[0].content == "Some text"
        assert results[0].distance == 0.15

    @pytest.mark.asyncio
    async def test_search_returns_empty(self) -> None:
        session_factory, session = _make_mock_session_factory()

        result_proxy = MagicMock()
        result_proxy.all.return_value = []
        session.execute.return_value = result_proxy

        store = PgVectorStore(session_factory=session_factory)
        results = await store.search(
            query_embedding=[0.1], top_k=5, owner_key_hash="a" * 64
        )

        assert results == []

    @pytest.mark.asyncio
    async def test_search_db_error_raises_storage_unavailable(self) -> None:
        """Database errors during search map to RAGStorageUnavailableError."""
        from sqlalchemy.exc import OperationalError

        session_factory, session = _make_mock_session_factory()
        session.execute.side_effect = OperationalError(
            "connection lost", {}, Exception("driver error")
        )

        store = PgVectorStore(session_factory=session_factory)

        from app.exceptions.base import RAGStorageUnavailableError

        with pytest.raises(RAGStorageUnavailableError, match="RAG storage unavailable"):
            await store.search(query_embedding=[0.1], top_k=5, owner_key_hash="a" * 64)

    @pytest.mark.asyncio
    async def test_add_document_unexpected_integrity_error_raises_storage_unavailable(
        self,
    ) -> None:
        """IntegrityErrors from unexpected constraints should map to
        RAGStorageUnavailableError, not ConflictError."""
        from sqlalchemy.exc import IntegrityError

        session_factory, session = _make_mock_session_factory()

        # Mock: no existing model, SHA-256, or path match.
        model_result = MagicMock()
        model_result.all.return_value = []
        scalar_result = MagicMock()
        scalar_result.scalar_one_or_none.return_value = None
        session.execute.side_effect = [
            MagicMock(),
            model_result,
            scalar_result,
            scalar_result,
        ]

        # Simulate a NOT NULL constraint violation (no constraint name
        # matching our expected ones)
        session.flush.side_effect = IntegrityError(
            'null value in column "embedding_model" violates not-null constraint',
            {},
            Exception("driver error"),
        )

        store = PgVectorStore(session_factory=session_factory)

        from app.exceptions.base import RAGStorageUnavailableError

        with pytest.raises(
            RAGStorageUnavailableError, match="RAG storage integrity error"
        ):
            await store.add_document(
                source_path="test.txt",
                content_sha256="abc123",
                embedding_model="nomic-embed-text",
                embedding_dimensions=768,
                chunks=["hello"],
                embeddings=[[0.1]],
                owner_key_hash="a" * 64,
            )
