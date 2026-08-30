"""Tests for multi-agent orchestration."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.multi_agent.models import (
    AgentRole,
    FailurePolicy,
    OrchestrationConfig,
    OrchestrationStatus,
    SharedContext,
    Subtask,
    SubtaskResult,
    SupervisorDecision,
    TaskStatus,
)
from app.multi_agent.orchestrator import Orchestrator
from app.multi_agent.supervisor import Supervisor

# ── Supervisor tests ────────────────────────────────────────────────────────


class TestSupervisor:
    """Tests for Supervisor task decomposition."""

    def test_parse_valid_json(self) -> None:
        """Supervisor parses valid JSON response."""
        supervisor = Supervisor.__new__(Supervisor)
        content = """
        {
            "reasoning": "Split into research and writing",
            "subtasks": [
                {
                    "id": "task_1",
                    "description": "Research the topic",
                    "agent_role": "research",
                    "depends_on": [],
                    "priority": 0
                },
                {
                    "id": "task_2",
                    "description": "Write the report",
                    "agent_role": "writer",
                    "depends_on": ["task_1"],
                    "input_template": "Based on: {prev_results}",
                    "priority": 1
                }
            ]
        }
        """
        decision = supervisor._parse_decision(content, max_subtasks=5)
        assert len(decision.subtasks) == 2
        assert decision.subtasks[0].agent_role == AgentRole.RESEARCH
        assert decision.subtasks[1].agent_role == AgentRole.WRITER
        assert decision.subtasks[1].depends_on == ("task_1",)

    def test_parse_json_in_markdown(self) -> None:
        """Supervisor extracts JSON from markdown code blocks."""
        supervisor = Supervisor.__new__(Supervisor)
        content = (
            '{"reasoning": "test", "subtasks":'
            ' [{"id": "t1", "description": "do it",'
            ' "agent_role": "writer"}]}'
        )
        decision = supervisor._parse_decision(content, max_subtasks=5)
        assert len(decision.subtasks) == 1
        assert decision.subtasks[0].id == "t1"

    def test_parse_invalid_json_fallback(self) -> None:
        """Supervisor falls back to single task on invalid JSON."""
        supervisor = Supervisor.__new__(Supervisor)
        decision = supervisor._parse_decision("not json at all", max_subtasks=5)
        assert len(decision.subtasks) == 1
        assert decision.subtasks[0].agent_role == AgentRole.WRITER

    def test_parse_max_subtasks_limit(self) -> None:
        """Supervisor respects max_subtasks limit."""
        supervisor = Supervisor.__new__(Supervisor)
        subtasks = [
            {"id": f"t{i}", "description": f"task {i}", "agent_role": "writer"}
            for i in range(10)
        ]
        content = json.dumps({"reasoning": "test", "subtasks": subtasks})
        decision = supervisor._parse_decision(content, max_subtasks=3)
        assert len(decision.subtasks) == 3


# ── SharedContext tests ─────────────────────────────────────────────────────


class TestSharedContext:
    """Tests for SharedContext result formatting."""

    def test_get_prev_results_empty(self) -> None:
        """Empty task_ids returns empty string."""
        ctx = SharedContext()
        assert ctx.get_prev_results(()) == ""

    def test_get_prev_results_with_completed(self) -> None:
        """Completed results are formatted correctly."""
        ctx = SharedContext(
            task_results={
                "t1": SubtaskResult(
                    task_id="t1",
                    status=TaskStatus.COMPLETED,
                    output="research findings",
                )
            }
        )
        result = ctx.get_prev_results(("t1",))
        assert "[t1]: research findings" in result

    def test_get_prev_results_with_failed(self) -> None:
        """Failed results show error message."""
        ctx = SharedContext(
            task_results={
                "t1": SubtaskResult(
                    task_id="t1",
                    status=TaskStatus.FAILED,
                    error="timeout",
                )
            }
        )
        result = ctx.get_prev_results(("t1",))
        assert "FAILED - timeout" in result


# ── Orchestrator tests ──────────────────────────────────────────────────────


class TestOrchestrator:
    """Tests for Orchestrator execution."""

    @pytest.mark.asyncio
    async def test_single_task_success(self) -> None:
        """Single task with no dependencies completes successfully."""
        mock_chat = AsyncMock()
        mock_response = MagicMock()
        mock_response.message.content = "task result"
        mock_response.prompt_tokens = 10
        mock_response.completion_tokens = 20
        mock_chat.chat.return_value = mock_response

        orchestrator = Orchestrator(chat_service=mock_chat)
        decision = SupervisorDecision(
            subtasks=[
                Subtask(
                    id="t1", description="do something", agent_role=AgentRole.WRITER
                )
            ]
        )
        result = await orchestrator.execute(decision, "user input")
        assert result.status == OrchestrationStatus.COMPLETED
        assert result.final_output == "task result"
        assert result.total_token_usage == 30

    @pytest.mark.asyncio
    async def test_dependent_tasks_sequential(self) -> None:
        """Tasks with dependencies execute in order."""
        call_order: list[str] = []
        mock_chat = AsyncMock()

        async def mock_chat_side_effect(request: object) -> MagicMock:
            # Extract task description from message
            msg = str(request.message) if hasattr(request, "message") else ""
            call_order.append(msg[:20])
            resp = MagicMock()
            resp.message.content = f"result for {msg[:20]}"
            resp.prompt_tokens = 5
            resp.completion_tokens = 5
            return resp

        mock_chat.chat.side_effect = mock_chat_side_effect

        orchestrator = Orchestrator(chat_service=mock_chat)
        decision = SupervisorDecision(
            subtasks=[
                Subtask(
                    id="t1", description="first task", agent_role=AgentRole.RESEARCH
                ),
                Subtask(
                    id="t2",
                    description="second task",
                    agent_role=AgentRole.WRITER,
                    depends_on=("t1",),
                ),
            ]
        )
        result = await orchestrator.execute(decision, "user input")
        assert result.status == OrchestrationStatus.COMPLETED
        assert len(result.subtask_results) == 2
        # t1 should execute before t2
        assert "first task" in call_order[0]

    @pytest.mark.asyncio
    async def test_fail_fast_policy(self) -> None:
        """FAIL_FAST policy stops on first failure."""
        mock_chat = AsyncMock()
        call_count = 0

        async def fail_first(request: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("agent failed")
            resp = MagicMock()
            resp.message.content = "ok"
            resp.prompt_tokens = 1
            resp.completion_tokens = 1
            return resp

        mock_chat.chat.side_effect = fail_first

        orchestrator = Orchestrator(chat_service=mock_chat)
        decision = SupervisorDecision(
            subtasks=[
                Subtask(
                    id="t1", description="will fail", agent_role=AgentRole.RESEARCH
                ),
                Subtask(
                    id="t2", description="should skip", agent_role=AgentRole.WRITER
                ),
            ]
        )
        config = OrchestrationConfig(failure_policy=FailurePolicy.FAIL_FAST)
        result = await orchestrator.execute(decision, "input", config=config)
        assert result.status == OrchestrationStatus.FAILED
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_skip_policy(self) -> None:
        """SKIP policy continues after failure."""
        mock_chat = AsyncMock()
        call_count = 0

        async def fail_first(request: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("agent failed")
            resp = MagicMock()
            resp.message.content = "ok"
            resp.prompt_tokens = 1
            resp.completion_tokens = 1
            return resp

        mock_chat.chat.side_effect = fail_first

        orchestrator = Orchestrator(chat_service=mock_chat)
        decision = SupervisorDecision(
            subtasks=[
                Subtask(
                    id="t1", description="will fail", agent_role=AgentRole.RESEARCH
                ),
                Subtask(id="t2", description="should run", agent_role=AgentRole.WRITER),
            ]
        )
        config = OrchestrationConfig(failure_policy=FailurePolicy.SKIP)
        result = await orchestrator.execute(decision, "input", config=config)
        # With SKIP policy, run completes even if one task fails
        assert result.status == OrchestrationStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_concurrency_limit(self) -> None:
        """Concurrency limit is respected."""
        running_count = 0
        max_running = 0
        mock_chat = AsyncMock()

        async def track_concurrency(request: object) -> MagicMock:
            nonlocal running_count, max_running
            running_count += 1
            max_running = max(max_running, running_count)
            await asyncio.sleep(0.05)  # Simulate work
            running_count -= 1
            resp = MagicMock()
            resp.message.content = "done"
            resp.prompt_tokens = 1
            resp.completion_tokens = 1
            return resp

        mock_chat.chat.side_effect = track_concurrency

        orchestrator = Orchestrator(chat_service=mock_chat)
        decision = SupervisorDecision(
            subtasks=[
                Subtask(
                    id=f"t{i}", description=f"task {i}", agent_role=AgentRole.WRITER
                )
                for i in range(5)
            ]
        )
        config = OrchestrationConfig(max_concurrency=2)
        result = await orchestrator.execute(decision, "input", config=config)
        assert result.status == OrchestrationStatus.COMPLETED
        assert max_running <= 2
