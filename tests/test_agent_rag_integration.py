from __future__ import annotations

from collections.abc import Callable, Generator
from typing import cast

import pytest

import app.core.container as container
from app.quota.service import QuotaService
from app.rag.service import RAGService
from app.services.agent_service import AgentService
from app.services.chat_service import ChatService
from app.usage.collector import UsageCollector


@pytest.fixture()
def isolated_agent_container(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[Callable[[RAGService | None], AgentService], None, None]:
    def build(rag_service: RAGService | None) -> AgentService:
        monkeypatch.setattr(
            container,
            "provide_rag_service",
            lambda: rag_service,
        )
        monkeypatch.setattr(
            container,
            "provide_chat_service",
            lambda: cast(ChatService, object()),
        )
        monkeypatch.setattr(
            container,
            "provide_quota_service",
            lambda: cast(QuotaService, object()),
        )
        monkeypatch.setattr(
            container,
            "provide_usage_collector",
            lambda: cast(UsageCollector, object()),
        )
        container.provide_agent_service.cache_clear()
        return container.provide_agent_service()

    yield build
    container.provide_agent_service.cache_clear()


def _tool_names(service: AgentService) -> list[str]:
    schemas = service._tool_registry.export_schemas()  # noqa: SLF001
    return [
        str(cast(dict[str, object], schema["function"])["name"]) for schema in schemas
    ]


def test_agent_service_keeps_calculator_only_when_rag_is_disabled(
    isolated_agent_container: Callable[[RAGService | None], AgentService],
) -> None:
    service = isolated_agent_container(None)

    assert isinstance(service, AgentService)
    assert _tool_names(service) == ["calculator"]
    assert service._tool_registry.get("knowledge_search") is None  # noqa: SLF001


def test_agent_service_registers_knowledge_search_when_rag_is_available(
    isolated_agent_container: Callable[[RAGService | None], AgentService],
) -> None:
    rag_service = cast(RAGService, object())
    service = isolated_agent_container(rag_service)

    assert isinstance(service, AgentService)
    assert _tool_names(service) == ["calculator", "knowledge_search"]
    assert service._tool_registry.get("knowledge_search") is not None  # noqa: SLF001
