from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field

import pytest

from app.agents import (
    AgentDecision,
    AgentRuntime,
    AgentState,
    RunStatus,
    ToolCall,
)
from app.exceptions.base import KnowledgeBaseEmptyError, NoRelevantContextError
from app.rag.service import PreparedRAGRequest, RAGReference
from app.schemas.chat import ChatRequest
from app.tools import ToolExecutor, ToolRegistry
from app.tools.knowledge_search import KnowledgeSearchTool


@dataclass
class FakeRAGService:
    prepared: PreparedRAGRequest | None = None
    error: Exception | None = None
    prepare_calls: list[ChatRequest] = field(default_factory=list)

    async def prepare(self, request: ChatRequest) -> PreparedRAGRequest:
        self.prepare_calls.append(request)
        if self.error is not None:
            raise self.error
        if self.prepared is None:
            raise AssertionError("fake RAG service has no prepared response")
        return self.prepared


@dataclass
class KnowledgeAwareModel:
    responses: list[AgentDecision]
    seen_states: list[AgentState] = field(default_factory=list)
    tool_payloads: list[dict[str, object]] = field(default_factory=list)
    tool_contents: list[str] = field(default_factory=list)

    async def decide(self, state: AgentState) -> AgentDecision:
        self.seen_states.append(deepcopy(state))
        if len(state.messages) > 1:
            tool_message = state.messages[-1]
            assert tool_message.role == "tool"
            self.tool_contents.append(tool_message.content)
            try:
                payload = json.loads(tool_message.content)
            except json.JSONDecodeError:
                pass
            else:
                assert isinstance(payload, dict)
                self.tool_payloads.append(payload)
        if not self.responses:
            raise AssertionError("the scripted model ran out of responses")
        return self.responses.pop(0)


def _prepared_request() -> PreparedRAGRequest:
    return PreparedRAGRequest(
        enhanced_request=ChatRequest(message="ignored"),
        references=(
            RAGReference(
                document_id="document-1",
                chunk_id="chunk-1",
                chunk_index=2,
                content="Agent Runtime uses a bounded model-tool loop.",
                distance=0.12,
            ),
        ),
    )


def _runtime(
    rag_service: FakeRAGService,
    model: KnowledgeAwareModel,
) -> AgentRuntime:
    tool = KnowledgeSearchTool(rag_service)  # type: ignore[arg-type]
    executor = ToolExecutor(ToolRegistry([tool]))
    return AgentRuntime(model, tool_executor=executor)


@pytest.mark.asyncio
async def test_agent_calls_knowledge_search_and_uses_result_for_final_answer() -> None:
    rag_service = FakeRAGService(prepared=_prepared_request())
    model = KnowledgeAwareModel(
        responses=[
            AgentDecision(
                tool_calls=(
                    ToolCall(
                        call_id="knowledge-call-1",
                        name="knowledge_search",
                        arguments={"query": "How does the agent runtime work?"},
                    ),
                )
            ),
            AgentDecision(answer="Agent Runtime uses a bounded model-tool loop."),
        ]
    )

    result = await _runtime(rag_service, model).run(
        "How does the agent runtime work?",
        run_id="agent-rag-success",
    )

    assert result.status is RunStatus.COMPLETED
    assert result.answer == "Agent Runtime uses a bounded model-tool loop."
    assert len(rag_service.prepare_calls) == 1
    assert rag_service.prepare_calls[0].message == "How does the agent runtime work?"

    tool_result = result.state.steps[0].tool_results[0]
    assert tool_result.call_id == "knowledge-call-1"
    assert tool_result.name == "knowledge_search"
    assert tool_result.succeeded is True
    assert len(model.tool_payloads) == 1
    assert model.tool_payloads[0]["ok"] is True
    results = model.tool_payloads[0]["results"]
    assert isinstance(results, list)
    assert results[0]["content"] == "Agent Runtime uses a bounded model-tool loop."
    assert [message.role for message in model.seen_states[1].messages] == [
        "user",
        "tool",
    ]
    assert len(result.state.steps) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "error_code"),
    [
        (
            KnowledgeBaseEmptyError("postgres password=internal-secret"),
            "knowledge_base_empty",
        ),
        (
            NoRelevantContextError("distance threshold=0.35 internal-secret"),
            "no_relevant_context",
        ),
    ],
)
async def test_structured_knowledge_errors_are_safe_for_model_feedback(
    error: Exception,
    error_code: str,
) -> None:
    rag_service = FakeRAGService(error=error)
    model = KnowledgeAwareModel(
        responses=[
            AgentDecision(
                tool_calls=(
                    ToolCall(
                        call_id="knowledge-call-error",
                        name="knowledge_search",
                        arguments={"query": "find the relevant policy"},
                    ),
                )
            ),
            AgentDecision(
                answer="The knowledge base does not provide enough information."
            ),
        ]
    )

    result = await _runtime(rag_service, model).run("find the relevant policy")

    assert result.status is RunStatus.COMPLETED
    assert result.answer == "The knowledge base does not provide enough information."
    assert len(rag_service.prepare_calls) == 1
    assert len(model.tool_payloads) == 1
    payload = model.tool_payloads[0]
    assert payload["ok"] is False
    assert payload["error_code"] == error_code
    assert "internal-secret" not in json.dumps(payload, ensure_ascii=False)
    assert "distance threshold=0.35" not in json.dumps(payload, ensure_ascii=False)
    assert result.state.steps[0].tool_results[0].succeeded is True


@pytest.mark.asyncio
async def test_unexpected_tool_exception_is_normalized_without_internal_details() -> (
    None
):
    rag_service = FakeRAGService(error=RuntimeError("database token=internal-secret"))
    model = KnowledgeAwareModel(
        responses=[
            AgentDecision(
                tool_calls=(
                    ToolCall(
                        call_id="knowledge-call-failed",
                        name="knowledge_search",
                        arguments={"query": "search the knowledge base"},
                    ),
                )
            ),
            AgentDecision(answer="I could not access the knowledge base."),
        ]
    )

    result = await _runtime(rag_service, model).run("search the knowledge base")

    assert result.status is RunStatus.COMPLETED
    assert result.answer == "I could not access the knowledge base."
    assert len(model.tool_payloads) == 0
    assert model.tool_contents == ["Tool execution failed."]
    tool_message = result.state.messages[-2]
    assert tool_message.role == "tool"
    assert tool_message.content == "Tool execution failed."
    assert "internal-secret" not in tool_message.content

    tool_result = result.state.steps[0].tool_results[0]
    assert tool_result.succeeded is False
    assert tool_result.error == "tool_execution_failed"
    assert all(
        "internal-secret" not in text
        for text in (
            *(message.content for message in result.state.messages),
            *(event.message or "" for event in result.events),
        )
    )
