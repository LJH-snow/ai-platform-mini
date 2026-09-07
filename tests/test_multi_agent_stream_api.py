"""SSE and sync safety tests for multi-agent runs (M3 P1)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.api.multi_agent import (
    _provide_multi_agent_record_service,
    _provide_multi_agent_service,
    _public_error_text,
    _subtask_public_error,
    _to_stream_event,
)
from app.auth.hash import hash_api_key
from app.main import app
from app.multi_agent.events import MultiAgentEvent, MultiAgentEventKind
from app.multi_agent.models import (
    AgentRole,
    OrchestrationResult,
    OrchestrationStatus,
    SubtaskResult,
    TaskStatus,
)
from app.multi_agent.service import MultiAgentService
from app.schemas.chat import ChatMessage, ChatResponse

client = TestClient(app)
_AUTH_HEADERS = {"Authorization": "Bearer sk-test-integration"}
_RAW_SECRET = "api_key=sk-secret1234567890"
_RAW_PATH = "/home/user/secret/app.py"


def _result(
    status: OrchestrationStatus,
    error: str | None = None,
    error_code: str | None = None,
    final_output: str = "done",
) -> OrchestrationResult:
    return OrchestrationResult(
        run_id="run-1",
        status=status,
        final_output=final_output,
        subtask_results=[],
        total_token_usage=0,
        error=error,
        error_code=error_code,
        duration_ms=1,
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )


def _stub_service(result: OrchestrationResult) -> object:
    """Return a fake MultiAgentService that always yields ``result``."""

    class _Svc:
        async def run(self, *args: object, **kwargs: object) -> OrchestrationResult:
            del args, kwargs
            return result

    return _Svc()


class TestResultErrorCodePassthrough:
    """The API never guesses codes from raw text; producers declare them."""

    def test_declared_code_passes_through_untouched(self) -> None:
        cases = [
            (OrchestrationStatus.COMPLETED, None),
            (OrchestrationStatus.CANCELLED, "cancelled"),
            (OrchestrationStatus.TIMED_OUT, "timeout"),
            (OrchestrationStatus.BUDGET_EXCEEDED, "budget_exceeded"),
            (OrchestrationStatus.FAILED, "supervisor_failed"),
            (OrchestrationStatus.FAILED, "dependency_deadlock"),
            (OrchestrationStatus.FAILED, "subtask_failed"),
            (OrchestrationStatus.FAILED, "internal_error"),
        ]
        for status, code in cases:
            _override_sync(
                cast(
                    MultiAgentService,
                    _stub_service(
                        _result(status, f"raw {_RAW_SECRET} {_RAW_PATH}", code)
                    ),
                )
            )
            try:
                response = client.post(
                    "/api/v1/multi-agent/runs",
                    json={"message": "hello"},
                    headers=_AUTH_HEADERS,
                )
            finally:
                _clear_sync()
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["error_code"] == code
            assert "sk-secret" not in json.dumps(body)
            assert "/home/user" not in json.dumps(body)

    def test_missing_code_is_not_guessed_from_text(self) -> None:
        # 文案再像死锁/拆分失败，没有声明就保持 None，绝不反推。
        _override_sync(
            cast(
                MultiAgentService,
                _stub_service(
                    _result(OrchestrationStatus.FAILED, "Deadlock: pending tasks")
                ),
            )
        )
        try:
            response = client.post(
                "/api/v1/multi-agent/runs",
                json={"message": "hello"},
                headers=_AUTH_HEADERS,
            )
        finally:
            _clear_sync()
        assert response.status_code == 200, response.text
        assert response.json()["error_code"] is None


class TestPublicErrorText:
    def test_never_returns_raw(self) -> None:
        text = _public_error_text("internal_error")
        assert text is not None
        assert _RAW_SECRET not in text
        assert _RAW_PATH not in text
        assert _public_error_text(None) is None

    def test_subtask_error_is_generic(self) -> None:
        failed = SubtaskResult(
            task_id="t1",
            status=TaskStatus.FAILED,
            error=f"boom {_RAW_SECRET} {_RAW_PATH}",
            agent_role=AgentRole.WRITER,
        )
        text = _subtask_public_error(failed)
        assert text is not None
        assert "sk-secret" not in text
        assert "/home/user" not in text
        completed = SubtaskResult(
            task_id="t1",
            status=TaskStatus.COMPLETED,
            agent_role=AgentRole.WRITER,
        )
        assert _subtask_public_error(completed) is None


class _FailingService:
    async def run(self, *args: object, **kwargs: object) -> OrchestrationResult:
        del args, kwargs
        return OrchestrationResult(
            run_id="run-safe-1",
            status=OrchestrationStatus.FAILED,
            final_output="",
            subtask_results=[
                SubtaskResult(
                    task_id="t1",
                    status=TaskStatus.FAILED,
                    error=f"boom {_RAW_SECRET} {_RAW_PATH}",
                    agent_role=AgentRole.WRITER,
                    token_usage=0,
                    steps_taken=0,
                    duration_ms=1,
                )
            ],
            total_token_usage=0,
            error=f"Supervisor decomposition failed: {_RAW_PATH} {_RAW_SECRET}",
            error_code="supervisor_failed",
            duration_ms=1,
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )


def _override_sync(service: object) -> None:
    app.dependency_overrides[_provide_multi_agent_service] = lambda: service


def _clear_sync() -> None:
    app.dependency_overrides.pop(_provide_multi_agent_service, None)
    app.dependency_overrides.pop(_provide_multi_agent_record_service, None)


def test_sync_response_hides_raw_error() -> None:
    _override_sync(cast(MultiAgentService, _FailingService()))
    try:
        response = client.post(
            "/api/v1/multi-agent/runs",
            json={"message": "hello"},
            headers=_AUTH_HEADERS,
        )
    finally:
        _clear_sync()
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["error_code"] == "supervisor_failed"
    assert "sk-secret" not in json.dumps(body)
    assert "/home/user" not in json.dumps(body)
    assert body["subtask_results"][0]["error_code"] == "subtask_failed"
    assert "sk-secret" not in (body["subtask_results"][0]["error"] or "")


def test_endpoints_require_bearer() -> None:
    assert client.post(
        "/api/v1/multi-agent/runs", json={"message": "hi"}
    ).status_code in (
        401,
        403,
    )
    assert client.post(
        "/api/v1/multi-agent/runs/stream", json={"message": "hi"}
    ).status_code in (401, 403)
    assert client.get("/api/v1/multi-agent/runs").status_code in (401, 403)
    assert client.get("/api/v1/multi-agent/runs/unknown").status_code in (401, 403)


def _chat_response(content: str) -> ChatResponse:
    return ChatResponse(
        model="test-model",
        message=ChatMessage(role="assistant", content=content),
        done=True,
        done_reason="stop",
        prompt_tokens=1,
        completion_tokens=1,
    )


def test_service_observer_redacts_reasoning_and_output() -> None:
    supervisor_json = json.dumps(
        {
            "reasoning": f"plan with {_RAW_SECRET}",
            "subtasks": [
                {"id": "t1", "description": "do work", "agent_role": "writer"}
            ],
        }
    )
    chat = AsyncMock()
    chat.chat = AsyncMock(
        side_effect=[
            _chat_response(supervisor_json),
            _chat_response(f"answer with {_RAW_PATH}"),
        ]
    )
    service = MultiAgentService(chat_service=chat)
    seen: list[MultiAgentEvent] = []

    class Sink:
        async def on_event(self, event: MultiAgentEvent) -> None:
            seen.append(event)

    result = asyncio.run(
        service.run(user_input="hello", observer=Sink(), run_id="run-redact-1")
    )
    assert result.status == OrchestrationStatus.COMPLETED
    planned = [e for e in seen if e.kind == MultiAgentEventKind.SUBTASKS_PLANNED]
    assert len(planned) == 1
    assert planned[0].reasoning is not None
    assert "sk-secret" not in planned[0].reasoning
    completed = [e for e in seen if e.kind == MultiAgentEventKind.SUBTASK_COMPLETED]
    assert completed
    assert completed[0].output_summary is not None
    assert "/home/user" not in completed[0].output_summary
    kinds = [e.kind.value for e in seen]
    assert kinds[0] == "run_started"
    assert kinds[-1] == "run_completed"
    sequences = [e.sequence for e in seen]
    assert sequences == sorted(sequences)


def test_cancel_event_produces_cancelled_terminal() -> None:
    async def _slow_chat(request: object) -> ChatResponse:
        del request
        await asyncio.sleep(5)
        return _chat_response("late")

    chat = SimpleNamespace(chat=_slow_chat)
    service = MultiAgentService(
        chat_service=cast(AsyncMock, chat),
    )

    # Bypass supervisor by stubbing decompose to one task.
    async def _fake_decompose(
        user_input: str, *, model: str | None = None, max_subtasks: int = 5
    ) -> object:
        del user_input, model, max_subtasks
        from app.multi_agent.models import Subtask, SupervisorDecision

        return SupervisorDecision(
            subtasks=[
                Subtask(id="t1", description="slow", agent_role=AgentRole.WRITER)
            ],
            reasoning="test",
        )

    service._supervisor.decompose = _fake_decompose  # type: ignore[assignment]
    cancel = asyncio.Event()
    cancel.set()
    seen: list[MultiAgentEvent] = []

    class Sink:
        async def on_event(self, event: MultiAgentEvent) -> None:
            seen.append(event)

    result = asyncio.run(
        service.run(user_input="hi", observer=Sink(), cancel_event=cancel)
    )
    assert result.status == OrchestrationStatus.CANCELLED
    assert seen[-1].kind == MultiAgentEventKind.RUN_CANCELLED


def test_stream_projection_preserves_order_fields() -> None:
    event = MultiAgentEvent(
        run_id="run-1",
        kind=MultiAgentEventKind.SUBTASK_COMPLETED,
        sequence=3,
        task_id="t1",
        agent_role="writer",
        duration_ms=12,
        token_usage=5,
        output_summary="ok",
    )
    projected = _to_stream_event(event, "req-1")
    assert projected.event == "subtask_completed"
    assert projected.run_id == "run-1"
    assert projected.sequence == 3
    assert projected.task_id == "t1"
    assert hash_api_key("sk-test-integration") is not None


def test_stream_projection_answer_delta_fields() -> None:
    event = MultiAgentEvent(
        run_id="run-1",
        kind=MultiAgentEventKind.SUBTASK_ANSWER_DELTA,
        sequence=4,
        task_id="t1",
        agent_role="research",
        output_summary="partial",
        agent_event_kind="answer_delta",
        step_index=1,
    )
    projected = _to_stream_event(event, "req-1")
    assert projected.event == "subtask_answer_delta"
    assert projected.output_summary == "partial"
    assert projected.agent_event_kind == "answer_delta"
    assert projected.step_index == 1


def test_stream_projection_step_tool_fields() -> None:
    event = MultiAgentEvent(
        run_id="run-1",
        kind=MultiAgentEventKind.SUBTASK_TOOL_COMPLETED,
        sequence=5,
        task_id="t1",
        agent_role="research",
        step_index=0,
        tool_name="knowledge_search",
        call_id="call-1",
        output_summary="3 sources",
    )
    projected = _to_stream_event(event, "req-1")
    assert projected.event == "subtask_tool_completed"
    assert projected.task_id == "t1"
    assert projected.step_index == 0
    assert projected.tool_name == "knowledge_search"
    assert projected.call_id == "call-1"
    assert projected.output_summary == "3 sources"


class _CaptureRecordService:
    """Record service double that captures save() kwargs for assertions."""

    def __init__(self) -> None:
        self.saved: dict[str, object] = {}

    async def save(self, result: object, **kwargs: object) -> None:
        del result
        self.saved = dict(kwargs)


def test_sync_persists_redacted_bounded_output() -> None:
    raw_output = f"answer with {_RAW_SECRET} {_RAW_PATH} " + "x" * 9000
    _override_sync(
        cast(
            MultiAgentService,
            _stub_service(
                _result(
                    OrchestrationStatus.COMPLETED,
                    error=None,
                    error_code=None,
                    final_output=raw_output,
                )
            ),
        )
    )
    capture = _CaptureRecordService()
    app.dependency_overrides[_provide_multi_agent_record_service] = lambda: capture
    try:
        response = client.post(
            "/api/v1/multi-agent/runs",
            json={"message": "hello"},
            headers=_AUTH_HEADERS,
        )
    finally:
        _clear_sync()
    assert response.status_code == 200, response.text
    body = response.json()
    assert "sk-secret" not in body["final_output"]
    assert "/home/user" not in body["final_output"]
    assert len(body["final_output"]) <= 8192
    persisted = capture.saved.get("final_output")
    assert isinstance(persisted, str)
    assert "sk-secret" not in persisted
    assert "/home/user" not in persisted
    assert len(persisted) <= 8192
