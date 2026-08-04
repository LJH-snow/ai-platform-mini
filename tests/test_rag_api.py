from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.api.rag import get_rag_service
from app.exceptions.base import (
    KnowledgeBaseEmptyError,
    ProviderUnavailableError,
    RAGStorageUnavailableError,
)
from app.main import app
from app.rag.service import PreparedRAGRequest, RAGService
from app.schemas.chat import ChatMessage, ChatRequest, ChatResponse

_AUTH_HEADERS = {"Authorization": "Bearer sk-test-integration"}


def _make_chat_response(content: str = "Test answer") -> ChatResponse:
    return ChatResponse(
        model="test-model",
        created_at="2026-01-01T00:00:00Z",
        message=ChatMessage(role="assistant", content=content),
        done=True,
        done_reason="stop",
        prompt_tokens=10,
        completion_tokens=20,
    )


def _make_prepared(
    message: str = "test",
    system_prompt: str | None = None,
    chunk_ids: tuple[str, ...] | None = None,
    messages: tuple[tuple[str, str], ...] | None = None,
) -> PreparedRAGRequest:
    req = ChatRequest(message=message, system_prompt=system_prompt)
    return PreparedRAGRequest(
        enhanced_request=req,
        chunk_ids=chunk_ids or ("c1",),
        messages=messages or (("system", "ctx"), ("user", message)),
    )


class TestRAGAPIAuth:
    def test_rag_endpoint_requires_auth(self) -> None:
        """RAG endpoint must return 401/403 without a valid API key.

        We override get_rag_service so RAG appears enabled,
        ensuring auth is checked before the RAG-unavailable path.
        """
        mock_rag = AsyncMock(spec=RAGService)
        mock_rag.prepare = AsyncMock(return_value=_make_prepared())
        mock_rag.answer = AsyncMock(return_value=_make_chat_response())

        # Remove the conftest auth override so real auth runs
        from app.auth.dependencies import provide_api_key_service

        real_override = app.dependency_overrides.pop(provide_api_key_service, None)
        app.dependency_overrides[get_rag_service] = lambda: mock_rag

        try:
            from fastapi.testclient import TestClient

            response = TestClient(app).post(
                "/api/v1/chat/rag",
                json={"message": "test"},
            )
            # Missing/invalid key → 401 or 403
            assert response.status_code in (401, 403)
        finally:
            # Restore auth override for other tests
            if real_override is not None:
                app.dependency_overrides[provide_api_key_service] = real_override
            app.dependency_overrides.pop(get_rag_service, None)

    def test_rag_disabled_unauthenticated_returns_401_not_503(self) -> None:
        """When RAG is disabled AND no auth is provided, the response
        must be 401/403 (auth failure), NOT 503 (RAG unavailable).

        This prevents unauthenticated users from probing whether
        RAG is enabled via the error code.
        """
        # Remove auth override so real auth runs
        from app.auth.dependencies import provide_api_key_service

        real_override = app.dependency_overrides.pop(provide_api_key_service, None)

        try:
            with TestClient(app) as client:
                response = client.post(
                    "/api/v1/chat/rag",
                    json={"message": "test"},
                )
                # Must be auth error, not RAG unavailable
                assert response.status_code in (401, 403)
        finally:
            if real_override is not None:
                app.dependency_overrides[provide_api_key_service] = real_override


class TestRAGAPIUnavailable:
    def test_rag_disabled_returns_503(self) -> None:
        """When RAG is not enabled, the endpoint returns 503."""
        # get_rag_service raises RAGUnavailableError when disabled
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/chat/rag",
                json={"message": "test"},
                headers=_AUTH_HEADERS,
            )
        # In default config RAG_ENABLED=false, so this should 503
        assert response.status_code == 503
        assert response.json()["code"] == "RAG_UNAVAILABLE"


class TestRAGAPISuccessFlow:
    def test_rag_endpoint_returns_chat_response(self) -> None:
        """Successful RAG request returns ChatResponse with content."""
        mock_rag = AsyncMock(spec=RAGService)
        mock_rag.prepare = AsyncMock(
            return_value=_make_prepared(
                chunk_ids=("c1",),
                messages=(("system", "RAG context here"), ("user", "What is X?")),
            )
        )
        mock_rag.answer = AsyncMock(return_value=_make_chat_response("X is Y"))

        mock_quota = AsyncMock()
        mock_quota.reserve = AsyncMock(return_value=None)

        mock_collector = AsyncMock()
        mock_collector.record_chat = AsyncMock()

        app.dependency_overrides[get_rag_service] = lambda: mock_rag
        from app.core.container import provide_quota_service, provide_usage_collector

        app.dependency_overrides[provide_quota_service] = lambda: mock_quota
        app.dependency_overrides[provide_usage_collector] = lambda: mock_collector

        try:
            with TestClient(app) as client:
                response = client.post(
                    "/api/v1/chat/rag",
                    json={"message": "What is X?"},
                    headers=_AUTH_HEADERS,
                )
            assert response.status_code == 200
            data = response.json()
            assert data["message"]["content"] == "X is Y"
        finally:
            app.dependency_overrides = {}

    def test_quota_reserve_receives_prepared_messages(self) -> None:
        """Quota reserve must receive prompt tokens estimated from
        the FINAL messages (including RAG context), not the raw request."""
        mock_rag = AsyncMock(spec=RAGService)
        prepared = _make_prepared(
            chunk_ids=("c1",),
            messages=(
                ("system", "Long RAG context " * 50),
                ("user", "What is X?"),
            ),
        )
        mock_rag.prepare = AsyncMock(return_value=prepared)
        mock_rag.answer = AsyncMock(return_value=_make_chat_response("X is Y"))

        mock_quota = AsyncMock()
        mock_quota.reserve = AsyncMock(return_value=None)
        mock_quota.settle = AsyncMock()

        mock_collector = AsyncMock()
        mock_collector.record_chat = AsyncMock()

        app.dependency_overrides[get_rag_service] = lambda: mock_rag
        from app.core.container import provide_quota_service, provide_usage_collector

        app.dependency_overrides[provide_quota_service] = lambda: mock_quota
        app.dependency_overrides[provide_usage_collector] = lambda: mock_collector

        try:
            with TestClient(app) as client:
                response = client.post(
                    "/api/v1/chat/rag",
                    json={"message": "What is X?"},
                    headers=_AUTH_HEADERS,
                )
            assert response.status_code == 200

            # Verify quota.reserve was called with prompt_tokens
            # that match the estimate from the prepared messages
            mock_quota.reserve.assert_called_once()
            call_kwargs = mock_quota.reserve.call_args[1]
            prompt_tokens = call_kwargs["prompt_tokens"]

            from app.quota.token_estimator import estimate_prompt_tokens

            expected_tokens = estimate_prompt_tokens(prepared.messages)
            assert prompt_tokens == expected_tokens

            # And the context-enhanced estimate must be significantly
            # larger than what we'd get from the raw user message alone.
            raw_estimate = estimate_prompt_tokens([("user", "What is X?")])
            assert (
                prompt_tokens > raw_estimate * 2
            )  # RAG context adds substantial tokens
        finally:
            app.dependency_overrides = {}

    def test_answer_not_called_when_quota_exceeded(self) -> None:
        """When quota is exceeded, answer() must not be called."""
        mock_rag = AsyncMock(spec=RAGService)
        mock_rag.prepare = AsyncMock(
            return_value=_make_prepared(messages=(("system", "ctx"), ("user", "test")))
        )
        mock_rag.answer = AsyncMock(return_value=_make_chat_response())

        from app.exceptions.base import QuotaExceededError

        mock_quota = AsyncMock()
        mock_quota.reserve = AsyncMock(side_effect=QuotaExceededError("Quota exceeded"))

        mock_collector = AsyncMock()
        mock_collector.record_chat = AsyncMock()

        app.dependency_overrides[get_rag_service] = lambda: mock_rag
        from app.core.container import provide_quota_service, provide_usage_collector

        app.dependency_overrides[provide_quota_service] = lambda: mock_quota
        app.dependency_overrides[provide_usage_collector] = lambda: mock_collector

        try:
            with TestClient(app) as client:
                response = client.post(
                    "/api/v1/chat/rag",
                    json={"message": "test"},
                    headers=_AUTH_HEADERS,
                )
            assert response.status_code == 429
            mock_rag.answer.assert_not_called()
        finally:
            app.dependency_overrides = {}


class TestRAGAPIErrorMapping:
    def test_empty_knowledge_base_returns_404(self) -> None:
        """KnowledgeBaseEmptyError from prepare() maps to 404."""
        mock_rag = AsyncMock(spec=RAGService)
        mock_rag.prepare = AsyncMock(
            side_effect=KnowledgeBaseEmptyError("No documents found")
        )

        mock_quota = AsyncMock()
        mock_quota.reserve = AsyncMock(return_value=None)

        mock_collector = AsyncMock()
        mock_collector.record_chat = AsyncMock()

        app.dependency_overrides[get_rag_service] = lambda: mock_rag
        from app.core.container import provide_quota_service, provide_usage_collector

        app.dependency_overrides[provide_quota_service] = lambda: mock_quota
        app.dependency_overrides[provide_usage_collector] = lambda: mock_collector

        try:
            with TestClient(app) as client:
                response = client.post(
                    "/api/v1/chat/rag",
                    json={"message": "test"},
                    headers=_AUTH_HEADERS,
                )
            assert response.status_code == 404
            assert response.json()["code"] == "KNOWLEDGE_BASE_EMPTY"
        finally:
            app.dependency_overrides = {}

    def test_embedding_failure_returns_502(self) -> None:
        """ProviderError from embedding maps to 502."""
        mock_rag = AsyncMock(spec=RAGService)
        mock_rag.prepare = AsyncMock(
            side_effect=ProviderUnavailableError("Embedding service down")
        )

        mock_quota = AsyncMock()
        mock_quota.reserve = AsyncMock(return_value=None)

        mock_collector = AsyncMock()
        mock_collector.record_chat = AsyncMock()

        app.dependency_overrides[get_rag_service] = lambda: mock_rag
        from app.core.container import provide_quota_service, provide_usage_collector

        app.dependency_overrides[provide_quota_service] = lambda: mock_quota
        app.dependency_overrides[provide_usage_collector] = lambda: mock_collector

        try:
            with TestClient(app) as client:
                response = client.post(
                    "/api/v1/chat/rag",
                    json={"message": "test"},
                    headers=_AUTH_HEADERS,
                )
            assert response.status_code == 502
        finally:
            app.dependency_overrides = {}

    def test_storage_unavailable_returns_503(self) -> None:
        """RAGStorageUnavailableError maps to 503 with RAG_STORAGE_UNAVAILABLE code."""
        mock_rag = AsyncMock(spec=RAGService)
        mock_rag.prepare = AsyncMock(
            side_effect=RAGStorageUnavailableError("Database unreachable")
        )

        mock_quota = AsyncMock()
        mock_quota.reserve = AsyncMock(return_value=None)

        mock_collector = AsyncMock()
        mock_collector.record_chat = AsyncMock()

        app.dependency_overrides[get_rag_service] = lambda: mock_rag
        from app.core.container import provide_quota_service, provide_usage_collector

        app.dependency_overrides[provide_quota_service] = lambda: mock_quota
        app.dependency_overrides[provide_usage_collector] = lambda: mock_collector

        try:
            with TestClient(app) as client:
                response = client.post(
                    "/api/v1/chat/rag",
                    json={"message": "test"},
                    headers=_AUTH_HEADERS,
                )
            assert response.status_code == 503
            assert response.json()["code"] == "RAG_STORAGE_UNAVAILABLE"
        finally:
            app.dependency_overrides = {}

    def test_prepare_before_quota_reserve(self) -> None:
        """prepare() must be called BEFORE quota.reserve(), so that
        the reserve includes RAG context tokens."""
        call_order: list[str] = []

        mock_rag = AsyncMock(spec=RAGService)

        async def _prepare(request: ChatRequest) -> PreparedRAGRequest:
            call_order.append("prepare")
            return _make_prepared(
                messages=(("system", "context"), ("user", request.message))
            )

        async def _answer(prepared: PreparedRAGRequest) -> ChatResponse:
            call_order.append("answer")
            return _make_chat_response()

        mock_rag.prepare = _prepare
        mock_rag.answer = _answer

        mock_quota = AsyncMock()

        async def _reserve(api_key_hash: str, **kwargs: object) -> None:
            call_order.append("reserve")

        mock_quota.reserve = _reserve
        mock_quota.settle = AsyncMock()

        mock_collector = AsyncMock()
        mock_collector.record_chat = AsyncMock()

        app.dependency_overrides[get_rag_service] = lambda: mock_rag
        from app.core.container import provide_quota_service, provide_usage_collector

        app.dependency_overrides[provide_quota_service] = lambda: mock_quota
        app.dependency_overrides[provide_usage_collector] = lambda: mock_collector

        try:
            with TestClient(app) as client:
                response = client.post(
                    "/api/v1/chat/rag",
                    json={"message": "test"},
                    headers=_AUTH_HEADERS,
                )
            assert response.status_code == 200
            # prepare → reserve → answer
            assert call_order.index("prepare") < call_order.index("reserve")
            assert call_order.index("reserve") < call_order.index("answer")
        finally:
            app.dependency_overrides = {}
