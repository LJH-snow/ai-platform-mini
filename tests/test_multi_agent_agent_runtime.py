"""M4 P1 tests: subtasks execute through AgentService/Runtime when wired."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.agents.models import (
    AgentMessage,
    AgentRunResult,
    AgentState,
    RunStatus,
    StopReason,
)
from app.auth.models import APIKey
from app.core.context import RequestContext
from app.multi_agent.models import (
    AgentRole,
    OrchestrationStatus,
    Subtask,
    SupervisorDecision,
)
from app.multi_agent.orchestrator import Orchestrator
from app.services.agent_service import AgentRunOutcome


def _outcome(
    *,
    status: RunStatus = RunStatus.COMPLETED,
    stop_reason: StopReason = StopReason.DIRECT_ANSWER,
    answer: str | None = "agent answer",
    prompt_tokens: int = 10,
    completion_tokens: int = 20,
) -> AgentRunOutcome:
    state = AgentState(
        run_id="agent-run",
        user_input="research",
        messages=[AgentMessage(role="user", content="research")],
    )
    result = AgentRunResult(
        run_id="agent-run",
        status=status,
        stop_reason=stop_reason,
        answer=answer,
        state=state,
        events=(),
        token_usage=prompt_tokens + completion_tokens,
    )
    return AgentRunOutcome(
        result=result,
        model="mock-model",
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        estimated_usage=False,
    )


def _context() -> tuple[RequestContext, APIKey]:
    return (
        RequestContext(request_id="req-1"),
        APIKey(key="key-hash", name="test"),
    )


class TestOrchestratorUsesAgentService:
    """The orchestrator delegates to AgentService when wired with context."""

    @pytest.mark.asyncio
    async def test_agent_service_executes_subtask(self) -> None:
        agent_service = AsyncMock()
        agent_service.run.return_value = _outcome()
        orchestrator = Orchestrator(agent_service=agent_service)
        decision = SupervisorDecision(
            subtasks=[
                Subtask(
                    id="t1",
                    description="research topic",
                    agent_role=AgentRole.RESEARCH,
                )
            ]
        )
        context, api_key = _context()
        result = await orchestrator.execute(
            decision,
            "user input",
            context=context,
            api_key=api_key,
        )
        assert result.status == OrchestrationStatus.COMPLETED
        assert result.final_output == "agent answer"
        assert result.total_token_usage == 30
        assert result.subtask_results[0].steps_taken == 0
        agent_service.run.assert_awaited_once()
        request = agent_service.run.await_args.args[0]
        assert request.model_fields_set == {"message", "system_prompt", "max_steps"}

    @pytest.mark.asyncio
    async def test_agent_id_is_forwarded(self) -> None:
        agent_service = AsyncMock()
        agent_service.run.return_value = _outcome()
        orchestrator = Orchestrator(agent_service=agent_service)
        decision = SupervisorDecision(
            subtasks=[
                Subtask(
                    id="t1",
                    description="write report",
                    agent_role=AgentRole.WRITER,
                    agent_id="agent-1",
                )
            ]
        )
        context, api_key = _context()
        await orchestrator.execute(
            decision,
            "user input",
            context=context,
            api_key=api_key,
        )
        request = agent_service.run.await_args.args[0]
        assert request.agent_id == "agent-1"
        assert "max_steps" not in request.model_fields_set
        assert "model" not in request.model_fields_set
        assert "system_prompt" not in request.model_fields_set

    @pytest.mark.asyncio
    async def test_stopped_agent_maps_to_failed_subtask(self) -> None:
        agent_service = AsyncMock()
        agent_service.run.return_value = _outcome(
            status=RunStatus.STOPPED,
            stop_reason=StopReason.TOKEN_BUDGET_EXCEEDED,
            answer=None,
            prompt_tokens=10,
            completion_tokens=10,
        )
        orchestrator = Orchestrator(agent_service=agent_service)
        decision = SupervisorDecision(
            subtasks=[
                Subtask(
                    id="t1",
                    description="research with budget",
                    agent_role=AgentRole.RESEARCH,
                )
            ]
        )
        context, api_key = _context()
        result = await orchestrator.execute(
            decision,
            "user input",
            context=context,
            api_key=api_key,
        )
        assert result.status == OrchestrationStatus.FAILED
        assert result.subtask_results[0].status.value == "failed"
        assert result.subtask_results[0].token_usage == 20

    @pytest.mark.asyncio
    async def test_missing_context_falls_back_to_chat(self) -> None:
        agent_service = AsyncMock()
        chat_service = AsyncMock()
        chat_service.chat.return_value = _chat_response("fallback answer")
        orchestrator = Orchestrator(
            chat_service=chat_service,
            agent_service=agent_service,
        )
        decision = SupervisorDecision(
            subtasks=[
                Subtask(
                    id="t1",
                    description="fallback",
                    agent_role=AgentRole.WRITER,
                )
            ]
        )
        result = await orchestrator.execute(decision, "user input")
        assert result.status == OrchestrationStatus.COMPLETED
        assert result.final_output == "fallback answer"
        agent_service.run.assert_not_awaited()
        chat_service.chat.assert_awaited_once()


def _chat_response(content: str) -> object:
    from types import SimpleNamespace

    return SimpleNamespace(
        message=SimpleNamespace(content=content),
        prompt_tokens=1,
        completion_tokens=1,
    )
