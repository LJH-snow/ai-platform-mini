"""Wiring tests for Sprint B review fixes (batch A).

Covers two review items:
1. AgentService now resolves model / max_steps / tool whitelist / prompt_ref
   from the stored Agent definition (previously the branch was dead code).
2. AgentDefinitionService enforces workspace ownership (IDOR fix) and
   validates prompt_ref references at create/update time.

All tests use in-memory backends, scripted model decisions and a fake
runtime so no LLM or Postgres is required.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import cast

import pytest

from app.agent_config.repository import InMemoryAgentDefinitionRepository
from app.agent_config.service import AgentDefinitionService
from app.agents import AgentModel, AgentRuntime, AgentTool
from app.agents.models import (
    AgentMessage,
    AgentRunResult,
    AgentState,
    RunStatus,
    StopReason,
)
from app.agents.protocols import StreamingAgentModel
from app.auth.identity import IdentityContext
from app.auth.models import APIKey
from app.core.context import RequestContext
from app.exceptions.base import RAGUnavailableError, ValidationError
from app.prompts.repository import InMemoryPromptRepository
from app.prompts.service import PromptRegistryService
from app.providers.results import ProviderChatResult
from app.quota.service import QuotaService
from app.runs.protocols import AgentEventObserver, RunTraceRecorderFactory
from app.schemas.agent import DEFAULT_AGENT_MAX_STEPS, AgentRunRequest
from app.schemas.chat import ChatMessage, ChatRequest, ChatResponse
from app.services.agent_service import AgentService
from app.services.chat_service import ChatService
from app.tools.calculator import CalculatorTool
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry
from app.usage.collector import UsageCollector

_WS_ID = "ws-1"
_OTHER_WS = "ws-2"


class _FakeKnowledgeSearchTool:
    """Registry-only stand-in for KnowledgeSearchTool (no RAG dependency)."""

    name = "knowledge_search"
    description = "Search an indexed knowledge base."
    input_schema: Mapping[str, object] = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }
    output_schema: Mapping[str, object] = {"type": "string"}

    async def execute(self, arguments: Mapping[str, object], context: object) -> str:
        del arguments, context
        return "fake knowledge result"


class _FakeChatService:
    default_model = "fallback-model"

    def __init__(self) -> None:
        self.requests: list[ChatRequest] = []
        self.stream_requests: list[ChatRequest] = []

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        return ChatResponse(
            model=request.model or self.default_model,
            message=ChatMessage(
                role="assistant",
                content='{"type":"final_answer","answer":"ok"}',
            ),
            done=True,
            prompt_tokens=4,
            completion_tokens=2,
        )

    async def chat_stream(
        self, request: ChatRequest
    ) -> AsyncIterator[ProviderChatResult]:
        """Yield one delta chunk and one terminal chunk per streamed answer."""
        self.stream_requests.append(request)
        model = request.model or self.default_model
        yield ProviderChatResult(
            model=model,
            created_at=None,
            role="assistant",
            content="streamed answer",
            done=False,
            done_reason=None,
            prompt_tokens=4,
            completion_tokens=0,
        )
        yield ProviderChatResult(
            model=model,
            created_at=None,
            role="assistant",
            content="",
            done=True,
            done_reason="stop",
            prompt_tokens=4,
            completion_tokens=2,
        )


class _FakeRuntime:
    def __init__(self, model: AgentModel, chat_service: _FakeChatService) -> None:
        self._model = model
        self._chat_service = chat_service
        self.kwargs: dict[str, object] = {}

    async def run(self, user_input: str, **kwargs: object) -> AgentRunResult:
        self.kwargs = kwargs
        state = AgentState(
            run_id="run-1",
            user_input=user_input,
            messages=[AgentMessage(role="user", content=user_input)],
        )
        answer: str | None = None
        if kwargs.get("stream_answer"):
            streaming_model = cast(StreamingAgentModel, self._model)
            chunks = [
                chunk.content
                async for chunk in streaming_model.stream_answer(state)
                if chunk.content
            ]
            answer = "".join(chunks)
        else:
            decision = await self._model.decide(state)
            answer = decision.answer
        return AgentRunResult(
            run_id="run-1",
            status=RunStatus.COMPLETED,
            stop_reason=StopReason.DIRECT_ANSWER,
            answer=answer,
            state=state,
            events=(),
            token_usage=0,
        )


@dataclass
class _RecordingRuntimeFactory:
    chat_service: _FakeChatService
    runtimes: list[_FakeRuntime] = field(default_factory=list)

    def __call__(
        self,
        model: AgentModel,
        tools: Mapping[str, AgentTool] | None,
        *,
        tool_executor: ToolExecutor | None = None,
        recorder_factory: RunTraceRecorderFactory | None = None,
        observer: AgentEventObserver | None = None,
    ) -> AgentRuntime:
        del tools, recorder_factory, observer
        runtime = _FakeRuntime(model, self.chat_service)
        self.runtimes.append(runtime)
        return cast(AgentRuntime, runtime)


class _FakeQuotaService:
    reservation_renewal_seconds = 60

    async def reserve(
        self,
        api_key_hash: str,
        max_tokens: int | None = None,
        prompt_tokens: int = 0,
    ) -> object:
        del api_key_hash, max_tokens, prompt_tokens
        from app.quota.models import QuotaReservation

        return QuotaReservation(
            reservation_id="reservation-1",
            api_key_hash="hashed",
            reserved_tokens=100,
            usage_date="2026-01-01",
        )

    async def extend(self, reservation_id: str, additional_tokens: int) -> None:
        del reservation_id, additional_tokens

    async def settle(self, reservation_id: str) -> None:
        del reservation_id

    async def release(self, reservation_id: str) -> None:
        del reservation_id

    async def renew(self, reservation_id: str) -> bool:
        del reservation_id
        return True


class _RecordingUsageCollector:
    async def record_chat(self, **kwargs: object) -> None:
        del kwargs


def _tool_registry() -> ToolRegistry:
    return ToolRegistry([CalculatorTool(), _FakeKnowledgeSearchTool()])


def _context(workspace_id: str | None = _WS_ID) -> RequestContext:
    return RequestContext(
        request_id="req-1",
        identity=IdentityContext(
            user_id="user-1",
            workspace_id=workspace_id,
            api_key_id=None,
            api_key_hash="key-hash",
            role="admin",
        ),
    )


def _api_key() -> APIKey:
    return APIKey(key="key-hash", name="test-key")


async def _make_service(
    *,
    agent_svc: AgentDefinitionService | None = None,
    prompt_registry: PromptRegistryService | None = None,
) -> tuple[AgentService, _FakeChatService, _RecordingRuntimeFactory]:
    chat = _FakeChatService()
    factory = _RecordingRuntimeFactory(chat)
    service = AgentService(
        chat_service=cast(ChatService, chat),
        quota_service=cast(QuotaService, _FakeQuotaService()),
        usage_collector=cast(UsageCollector, _RecordingUsageCollector()),
        runtime_factory=factory,
        tool_registry=_tool_registry(),
        granted_permissions=frozenset(),
        prompt_registry=prompt_registry,
        agent_definition_service=agent_svc,
    )
    return service, chat, factory


async def _make_agent_svc(
    prompt_registry: PromptRegistryService | None = None,
) -> AgentDefinitionService:
    return AgentDefinitionService(
        repository=InMemoryAgentDefinitionRepository(),
        tool_registry=_tool_registry(),
        prompt_registry=prompt_registry,
    )


# ── Agent definition resolution (review item 1) ───────────────────────────


async def test_agent_id_resolves_model_max_steps_and_tool_whitelist() -> None:
    agent_svc = await _make_agent_svc()
    record, _ = await agent_svc.create_agent(
        workspace_id=_WS_ID,
        name="calculator-agent",
        model="def-model",
        prompt_ref="",
        tool_names=["calculator"],
        max_steps=9,
    )
    service, chat, factory = await _make_service(agent_svc=agent_svc)

    outcome = await service.run(
        AgentRunRequest(message="hello", agent_id=record.id),
        context=_context(),
        api_key=_api_key(),
    )

    assert outcome.model == "def-model"
    # The request default max_steps is not an explicit override → definition wins.
    assert factory.runtimes[0].kwargs["max_steps"] == 9
    system_prompt = chat.requests[0].system_prompt or ""
    assert "calculator" in system_prompt
    assert "knowledge_search" not in system_prompt


async def test_explicit_request_model_overrides_agent_definition() -> None:
    agent_svc = await _make_agent_svc()
    await agent_svc.create_agent(
        workspace_id=_WS_ID,
        name="a",
        model="def-model",
        prompt_ref="",
        max_steps=9,
    )
    service, chat, _ = await _make_service(agent_svc=agent_svc)

    await service.run(
        AgentRunRequest(message="hello", model="explicit-model"),
        context=_context(),
        api_key=_api_key(),
    )

    assert chat.requests[0].model == "explicit-model"


async def test_agent_id_without_definition_service_runs_plain() -> None:
    # Legacy path: no agent_definition_service injected → agent_id ignored.
    service, chat, factory = await _make_service(agent_svc=None)

    outcome = await service.run(
        AgentRunRequest(message="hello", agent_id="some-agent"),
        context=_context(),
        api_key=_api_key(),
    )

    assert outcome.model == "fallback-model"
    assert factory.runtimes[0].kwargs["max_steps"] == DEFAULT_AGENT_MAX_STEPS
    assert chat.requests[0].model is None


async def test_unknown_agent_id_raises() -> None:
    agent_svc = await _make_agent_svc()
    service, _, _ = await _make_service(agent_svc=agent_svc)

    with pytest.raises(ValidationError):
        await service.run(
            AgentRunRequest(message="hello", agent_id="missing"),
            context=_context(),
            api_key=_api_key(),
        )


async def test_disabled_agent_raises() -> None:
    agent_svc = await _make_agent_svc()
    await agent_svc.create_agent(
        workspace_id=_WS_ID,
        name="a",
        model="m",
        prompt_ref="",
    )
    # Locate the created agent and disable it.
    record = (await agent_svc.list_agents(_WS_ID))[0]
    await agent_svc.update_agent(record.id, workspace_id=_WS_ID, enabled=False)
    service, _, _ = await _make_service(agent_svc=agent_svc)

    with pytest.raises(ValidationError):
        await service.run(
            AgentRunRequest(message="hello", agent_id=record.id),
            context=_context(),
            api_key=_api_key(),
        )


async def test_agent_from_other_workspace_is_not_accessible() -> None:
    agent_svc = await _make_agent_svc()
    await agent_svc.create_agent(
        workspace_id=_OTHER_WS,
        name="other",
        model="m",
        prompt_ref="",
    )
    record = (await agent_svc.list_agents(_OTHER_WS))[0]
    service, _, _ = await _make_service(agent_svc=agent_svc)

    with pytest.raises(ValidationError):
        await service.run(
            AgentRunRequest(message="hello", agent_id=record.id),
            context=_context(workspace_id=_WS_ID),
            api_key=_api_key(),
        )


async def test_rag_preset_without_knowledge_search_whitelist_raises() -> None:
    agent_svc = await _make_agent_svc()
    await agent_svc.create_agent(
        workspace_id=_WS_ID,
        name="calc-only",
        model="m",
        prompt_ref="",
        tool_names=["calculator"],
    )
    record = (await agent_svc.list_agents(_WS_ID))[0]
    service, _, _ = await _make_service(agent_svc=agent_svc)

    with pytest.raises(RAGUnavailableError):
        await service.run(
            AgentRunRequest(message="hello", agent_id=record.id, preset="rag"),
            context=_context(),
            api_key=_api_key(),
        )


async def test_prompt_ref_is_rendered_into_system_prompt() -> None:
    registry = PromptRegistryService(repository=InMemoryPromptRepository())
    await registry.seed(name="custom_prompt", content="CUSTOM PROMPT CONTENT")
    agent_svc = await _make_agent_svc(prompt_registry=registry)
    record, _ = await agent_svc.create_agent(
        workspace_id=_WS_ID,
        name="a",
        model="m",
        prompt_ref="custom_prompt",
    )
    service, chat, _ = await _make_service(
        agent_svc=agent_svc, prompt_registry=registry
    )

    await service.run(
        AgentRunRequest(message="hello", agent_id=record.id),
        context=_context(),
        api_key=_api_key(),
    )

    system_prompt = chat.requests[0].system_prompt or ""
    assert "CUSTOM PROMPT CONTENT" in system_prompt
    # The decision protocol layer must still be present underneath.
    assert "decision model" in system_prompt


async def test_stream_answer_uses_agent_definition_base_prompt() -> None:
    """Streaming final answers must carry the same prompt layers as decide()."""
    registry = PromptRegistryService(repository=InMemoryPromptRepository())
    await registry.seed(name="custom_prompt", content="CUSTOM PROMPT CONTENT")
    agent_svc = await _make_agent_svc(prompt_registry=registry)
    record, _ = await agent_svc.create_agent(
        workspace_id=_WS_ID,
        name="a",
        model="m",
        prompt_ref="custom_prompt",
    )
    service, chat, _ = await _make_service(
        agent_svc=agent_svc, prompt_registry=registry
    )

    class _NoopObserver:
        def observe(self, event: object) -> None:
            del event

    await service.run(
        AgentRunRequest(message="hello", agent_id=record.id),
        context=_context(),
        api_key=_api_key(),
        observer=_NoopObserver(),
        streaming=True,
    )

    assert chat.stream_requests
    system_prompt = chat.stream_requests[0].system_prompt or ""
    assert "CUSTOM PROMPT CONTENT" in system_prompt
    # The decision protocol layer must still be present underneath.
    assert "decision model" in system_prompt


# ── Workspace ownership (review item 8, IDOR) ─────────────────────────────


async def test_get_agent_rejects_other_workspace() -> None:
    agent_svc = await _make_agent_svc()
    await agent_svc.create_agent(
        workspace_id=_OTHER_WS, name="a", model="m", prompt_ref=""
    )
    record = (await agent_svc.list_agents(_OTHER_WS))[0]

    assert await agent_svc.get_agent(record.id, workspace_id=_WS_ID) is None
    assert await agent_svc.get_agent(record.id, workspace_id=_OTHER_WS) is not None


async def test_update_agent_rejects_other_workspace() -> None:
    agent_svc = await _make_agent_svc()
    await agent_svc.create_agent(
        workspace_id=_OTHER_WS, name="a", model="m", prompt_ref=""
    )
    record = (await agent_svc.list_agents(_OTHER_WS))[0]

    result = await agent_svc.update_agent(
        record.id, workspace_id=_WS_ID, name="hijacked"
    )
    assert result is None
    # Original record unchanged.
    original = await agent_svc.get_agent(record.id)
    assert original is not None
    assert original.name == "a"


async def test_delete_agent_rejects_other_workspace() -> None:
    agent_svc = await _make_agent_svc()
    await agent_svc.create_agent(
        workspace_id=_OTHER_WS, name="a", model="m", prompt_ref=""
    )
    record = (await agent_svc.list_agents(_OTHER_WS))[0]

    assert await agent_svc.delete_agent(record.id, workspace_id=_WS_ID) is False
    assert await agent_svc.get_agent(record.id) is not None


async def test_create_agent_rejects_missing_prompt_ref() -> None:
    registry = PromptRegistryService(repository=InMemoryPromptRepository())
    await registry.seed(name="existing", content="EXISTS")
    agent_svc = await _make_agent_svc(prompt_registry=registry)

    with pytest.raises(ValidationError):
        await agent_svc.create_agent(
            workspace_id=_WS_ID,
            name="a",
            model="m",
            prompt_ref="missing_template",
        )
    # Valid reference passes.
    record, _ = await agent_svc.create_agent(
        workspace_id=_WS_ID,
        name="b",
        model="m",
        prompt_ref="existing",
    )
    assert record.prompt_ref == "existing"


async def test_workspace_disabled_tool_removed_from_run_whitelist() -> None:
    """Tool Center disablement takes effect on existing agents immediately."""
    agent_svc = await _make_agent_svc()
    await agent_svc.create_agent(
        workspace_id=_WS_ID,
        name="calc-agent",
        model="m",
        prompt_ref="",
        tool_names=["calculator"],
    )
    agent_id = (await agent_svc.list_agents(_WS_ID))[0].id
    service, chat, _ = await _make_service(agent_svc=agent_svc)
    api_key = _api_key()
    context = _context()

    await service.run(
        AgentRunRequest(message="hello", agent_id=agent_id),
        context=context,
        api_key=api_key,
    )
    assert "calculator" in (chat.requests[0].system_prompt or "")

    await agent_svc.set_tool_enabled(_WS_ID, "calculator", False)

    await service.run(
        AgentRunRequest(message="hello", agent_id=agent_id),
        context=context,
        api_key=api_key,
    )
    assert "calculator" not in (chat.requests[-1].system_prompt or "")


async def test_pinned_prompt_ref_renders_exact_version() -> None:
    """Agent prompt_ref 'name@version' pins the version against activation."""
    registry = PromptRegistryService(repository=InMemoryPromptRepository())
    await registry.seed(name="custom_prompt", content="V1 CONTENT")
    await registry.create_version("custom_prompt", "V2 CONTENT")
    await registry.activate("custom_prompt", 2)
    agent_svc = await _make_agent_svc(prompt_registry=registry)
    record, _ = await agent_svc.create_agent(
        workspace_id=_WS_ID,
        name="a",
        model="m",
        prompt_ref="custom_prompt@1",
    )
    service, chat, _ = await _make_service(
        agent_svc=agent_svc, prompt_registry=registry
    )

    await service.run(
        AgentRunRequest(message="hello", agent_id=record.id),
        context=_context(),
        api_key=_api_key(),
    )

    system_prompt = chat.requests[0].system_prompt or ""
    # v2 is active but the agent is pinned to v1.
    assert "V1 CONTENT" in system_prompt
    assert "V2 CONTENT" not in system_prompt


async def test_pinned_prompt_ref_with_missing_version_is_rejected() -> None:
    registry = PromptRegistryService(repository=InMemoryPromptRepository())
    await registry.seed(name="custom_prompt", content="V1 CONTENT")
    agent_svc = await _make_agent_svc(prompt_registry=registry)

    with pytest.raises(ValidationError):
        await agent_svc.create_agent(
            workspace_id=_WS_ID,
            name="a",
            model="m",
            prompt_ref="custom_prompt@99",
        )
