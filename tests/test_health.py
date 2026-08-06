import asyncio
from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import require_api_key
from app.auth.models import APIKey
from app.core.container import (
    provide_llm_provider,
    provide_mcp_manager,
    provide_usage_service,
)
from app.exceptions.base import ProviderError, ProviderUnavailableError
from app.main import app
from app.mcp import (
    MCPReadiness,
    MCPReadinessState,
    MCPServerState,
    MCPServerStatus,
)
from app.usage.memory_repository import InMemoryUsageRepository
from app.usage.models import UsageRecord
from app.usage.service import UsageService

client = TestClient(app)


def test_health_check() -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_usage_returns_only_authenticated_key_summary() -> None:
    repository = InMemoryUsageRepository()
    usage_service = UsageService(repository)

    async def override_api_key() -> APIKey:
        return APIKey(key="hash1", name="key-1")

    app.dependency_overrides[require_api_key] = override_api_key
    app.dependency_overrides[provide_usage_service] = lambda: usage_service

    try:
        asyncio.run(
            usage_service.record(
                UsageRecord(
                    request_id="r1",
                    model="llama3",
                    total_tokens=30,
                    api_key_hash="hash1",
                )
            )
        )
        asyncio.run(
            usage_service.record(
                UsageRecord(
                    request_id="r2",
                    model="mistral",
                    total_tokens=300,
                    api_key_hash="hash2",
                )
            )
        )
        response = client.get("/api/v1/usage")
    finally:
        app.dependency_overrides.pop(require_api_key, None)
        app.dependency_overrides.pop(provide_usage_service, None)

    assert response.status_code == 200
    assert response.json()["total_requests"] == 1
    assert response.json()["total_tokens"] == 30
    assert set(response.json()["by_model"]) == {"llama3"}


# --- Fixture for readability tests using a mock provider ---


class _MockProvider:
    @property
    def default_model(self) -> str:
        return "mock-model"

    async def list_models(self) -> dict[str, object]:
        return {"models": [{"name": "mock-model"}]}

    async def close(self) -> None:
        pass


@pytest.fixture
def _override_provider() -> Generator[None, None, None]:
    app.dependency_overrides[provide_llm_provider] = lambda: _MockProvider()
    settings = MagicMock(auth_storage="memory", rag_enabled=False)
    with patch("app.api.health.get_settings", return_value=settings):
        yield
    app.dependency_overrides.pop(provide_llm_provider, None)


def _async_context(value: object) -> MagicMock:
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=value)
    context.__aexit__ = AsyncMock(return_value=False)
    return context


def _mock_engine() -> MagicMock:
    engine = MagicMock()
    conn = AsyncMock()
    conn.execute = AsyncMock()
    conn.commit = AsyncMock()
    engine.connect.return_value = _async_context(conn)
    return engine


_DISABLED_RAG: dict[str, str | bool | None] = {
    "enabled": False,
    "status": "disabled",
    "database": "not_checked",
    "database_reason": None,
    "embedding": "not_checked",
    "embedding_reason": None,
    "embedding_model": None,
}


# --- Readiness probe tests ---


def test_readiness_returns_200_when_provider_ok(
    _override_provider: None,
) -> None:
    response = client.get("/api/v1/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["rag"]["status"] == "disabled"


def test_readiness_returns_503_when_provider_fails() -> None:
    class _FailingProvider:
        @property
        def default_model(self) -> str:
            return "fail-model"

        async def list_models(self) -> dict[str, object]:
            raise ProviderError("unavailable")

        async def close(self) -> None:
            pass

    app.dependency_overrides[provide_llm_provider] = lambda: _FailingProvider()
    settings = MagicMock(auth_storage="memory", rag_enabled=False)
    try:
        with patch("app.api.health.get_settings", return_value=settings):
            response = client.get("/api/v1/ready")
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "not_ready"
        assert data["checks"]["provider"] == "failed"
        assert data["rag"]["enabled"] is False
    finally:
        app.dependency_overrides.pop(provide_llm_provider, None)


def test_readiness_memory_mode_no_database_check(
    _override_provider: None,
) -> None:
    response = client.get("/api/v1/ready")
    assert response.status_code == 200
    data = response.json()
    checks = data.get("checks", data)
    assert "database" not in checks


def test_mcp_health_and_readiness_share_lifecycle_boundary(
    _override_provider: None,
) -> None:
    manager = MagicMock()
    manager.readiness_status.return_value = MCPReadiness(
        state=MCPReadinessState.READY,
        servers=(
            MCPServerStatus(
                name="demo",
                state=MCPServerState.READY,
                tool_count=1,
            ),
        ),
    )
    app.dependency_overrides[provide_mcp_manager] = lambda: manager
    settings = MagicMock(
        mcp_enabled=True,
        auth_storage="memory",
        rag_enabled=False,
    )

    try:
        with patch("app.api.health.get_settings", return_value=settings):
            health_response = client.get("/api/v1/health/mcp")
            readiness_response = client.get("/api/v1/ready")
    finally:
        app.dependency_overrides.pop(provide_mcp_manager, None)

    assert health_response.status_code == 200
    assert health_response.json() == {
        "ready": True,
        "servers": [
            {
                "error_code": None,
                "name": "demo",
                "status": "ready",
                "tool_count": 1,
            }
        ],
        "status": "ready",
    }
    assert readiness_response.status_code == 200
    assert readiness_response.json() == {
        "status": "ready",
        "checks": {"provider": "ok", "mcp": "ready"},
        "rag": _DISABLED_RAG,
    }


def test_mcp_discovery_failure_does_not_block_application_readiness(
    _override_provider: None,
) -> None:
    manager = MagicMock()
    manager.readiness_status.return_value = MCPReadiness(
        state=MCPReadinessState.NOT_READY,
        servers=(
            MCPServerStatus(
                name="broken",
                state=MCPServerState.FAILED,
                error_code="discovery_failed",
            ),
        ),
    )
    app.dependency_overrides[provide_mcp_manager] = lambda: manager
    settings = MagicMock(mcp_enabled=True, auth_storage="memory", rag_enabled=False)

    try:
        with patch("app.api.health.get_settings", return_value=settings):
            readiness_response = client.get("/api/v1/ready")
            health_response = client.get("/api/v1/health/mcp")
    finally:
        app.dependency_overrides.pop(provide_mcp_manager, None)

    assert readiness_response.status_code == 200
    assert readiness_response.json() == {
        "status": "ready",
        "checks": {"provider": "ok", "mcp": "not_ready"},
        "rag": _DISABLED_RAG,
    }
    assert health_response.status_code == 503
    assert health_response.json()["status"] == "not_ready"
    assert health_response.json()["ready"] is False


def test_degraded_mcp_discovery_does_not_block_application_readiness(
    _override_provider: None,
) -> None:
    manager = MagicMock()
    manager.readiness_status.return_value = MCPReadiness(
        state=MCPReadinessState.DEGRADED,
        servers=(
            MCPServerStatus(
                name="broken",
                state=MCPServerState.FAILED,
                error_code="discovery_failed",
            ),
            MCPServerStatus(
                name="working",
                state=MCPServerState.READY,
                tool_count=1,
            ),
        ),
    )
    app.dependency_overrides[provide_mcp_manager] = lambda: manager
    settings = MagicMock(mcp_enabled=True, auth_storage="memory", rag_enabled=False)

    try:
        with patch("app.api.health.get_settings", return_value=settings):
            response = client.get("/api/v1/ready")
    finally:
        app.dependency_overrides.pop(provide_mcp_manager, None)

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"provider": "ok", "mcp": "degraded"},
        "rag": _DISABLED_RAG,
    }


def test_readiness_reports_rag_ready_when_enabled(
    _override_provider: None,
) -> None:
    settings = MagicMock(
        auth_storage="memory",
        rag_enabled=True,
        rag_embedding_model="nomic-embed-text",
        mcp_enabled=False,
    )
    engine = _mock_engine()
    embedder = AsyncMock()
    embedder.embed.return_value = [[0.1] * 768]

    with (
        patch("app.api.health.get_settings", return_value=settings),
        patch("app.db.init.get_engine", return_value=engine),
        patch("app.api.health.provide_embedder", return_value=embedder),
    ):
        response = client.get("/api/v1/ready")

    assert response.status_code == 200
    assert response.json()["rag"] == {
        "enabled": True,
        "status": "ready",
        "database": "ok",
        "database_reason": None,
        "embedding": "ok",
        "embedding_reason": None,
        "embedding_model": "nomic-embed-text",
    }
    embedder.embed.assert_awaited_once_with(["ping"])


def test_readiness_reports_rag_disabled_without_probing(
    _override_provider: None,
) -> None:
    settings = MagicMock(auth_storage="memory", rag_enabled=False, mcp_enabled=False)

    with (
        patch("app.api.health.get_settings", return_value=settings),
        patch("app.db.init.get_engine") as mock_engine,
        patch("app.api.health.provide_embedder") as mock_embedder,
    ):
        response = client.get("/api/v1/ready")

    assert response.status_code == 200
    assert response.json()["rag"] == _DISABLED_RAG
    mock_engine.assert_not_called()
    mock_embedder.assert_not_called()


def test_readiness_reports_database_unavailable_when_rag_enabled(
    _override_provider: None,
) -> None:
    settings = MagicMock(
        auth_storage="memory",
        rag_enabled=True,
        rag_embedding_model="nomic-embed-text",
        mcp_enabled=False,
    )
    engine = _mock_engine()
    conn = engine.connect.return_value.__aenter__.return_value
    conn.execute = AsyncMock(side_effect=RuntimeError("connection refused"))
    embedder = AsyncMock()

    with (
        patch("app.api.health.get_settings", return_value=settings),
        patch("app.db.init.get_engine", return_value=engine),
        patch("app.api.health.provide_embedder", return_value=embedder),
    ):
        response = client.get("/api/v1/ready")

    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "not_ready"
    assert data["rag"]["database"] == "unavailable"
    assert data["rag"]["database_reason"] == "connection_failed"
    assert data["rag"]["status"] == "database_unavailable"
    assert data["rag"]["embedding"] == "not_checked"
    embedder.embed.assert_not_awaited()


def test_readiness_reports_embedding_unavailable_when_rag_enabled(
    _override_provider: None,
) -> None:
    settings = MagicMock(
        auth_storage="memory",
        rag_enabled=True,
        rag_embedding_model="nomic-embed-text",
        mcp_enabled=False,
    )
    engine = _mock_engine()
    embedder = AsyncMock()
    embedder.embed.side_effect = ProviderUnavailableError(
        "http://ollama.internal:11434"
    )

    with (
        patch("app.api.health.get_settings", return_value=settings),
        patch("app.db.init.get_engine", return_value=engine),
        patch("app.api.health.provide_embedder", return_value=embedder),
    ):
        response = client.get("/api/v1/ready")

    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "not_ready"
    assert data["rag"]["database"] == "ok"
    assert data["rag"]["embedding"] == "unavailable"
    assert data["rag"]["embedding_reason"] == "connection_failed"
    assert data["rag"]["status"] == "embedding_unavailable"
    embedder.embed.assert_awaited_once_with(["ping"])


def test_readiness_preserves_provider_and_database_fields(
    _override_provider: None,
) -> None:
    settings = MagicMock(auth_storage="postgres", rag_enabled=False, mcp_enabled=False)
    engine = _mock_engine()

    with (
        patch("app.api.health.get_settings", return_value=settings),
        patch("app.db.init.get_engine", return_value=engine),
    ):
        response = client.get("/api/v1/ready")

    assert response.status_code == 200
    data = response.json()
    assert data["checks"]["provider"] == "ok"
    assert data["checks"]["database"] == "ok"
    assert data["rag"]["enabled"] is False
    assert "database" in data["checks"]


def test_readiness_does_not_leak_secrets_or_raw_provider_errors(
    _override_provider: None,
) -> None:
    settings = MagicMock(
        auth_storage="memory",
        rag_enabled=True,
        rag_embedding_model="nomic-embed-text",
        mcp_enabled=False,
    )
    engine = _mock_engine()
    conn = engine.connect.return_value.__aenter__.return_value
    conn.execute = AsyncMock(
        side_effect=RuntimeError("password=hunter2 host=db.internal")
    )
    embedder = AsyncMock()
    embedder.embed.side_effect = ProviderUnavailableError(
        "Bearer sk-secret http://embedding.internal/v1"
    )

    with (
        patch("app.api.health.get_settings", return_value=settings),
        patch("app.db.init.get_engine", return_value=engine),
        patch("app.api.health.provide_embedder", return_value=embedder),
    ):
        database_response = client.get("/api/v1/ready")

    database_body = str(database_response.json())
    assert "hunter2" not in database_body
    assert "db.internal" not in database_body

    ready_engine = _mock_engine()
    with (
        patch("app.api.health.get_settings", return_value=settings),
        patch("app.db.init.get_engine", return_value=ready_engine),
        patch("app.api.health.provide_embedder", return_value=embedder),
    ):
        embedding_response = client.get("/api/v1/ready")

    embedding_body = str(embedding_response.json())
    assert "sk-secret" not in embedding_body
    assert "embedding.internal" not in embedding_body
    assert "password" not in embedding_body
