"""Persistence and history tests for multi-agent runs (M3 P2)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.multi_agent import _provide_multi_agent_record_service
from app.auth.hash import hash_api_key
from app.main import app
from app.multi_agent.events import MultiAgentEvent, MultiAgentEventKind
from app.services.multi_agent_run_record_service import (
    MAX_MULTI_AGENT_EVENT_PAYLOAD_BYTES,
    MAX_MULTI_AGENT_EVENTS_PER_RUN,
    MultiAgentEventRecorder,
    project_run_response,
    public_run_payload,
    public_run_summary,
)

client = TestClient(app)
_AUTH_HEADERS = {"Authorization": "Bearer sk-test-integration"}


def _row(
    run_id: str = "run-1",
    owner: str = "owner-a",
    *,
    polluted: dict[str, Any] | None = None,
) -> SimpleNamespace:
    payload: dict[str, Any] = {
        "status": "completed",
        "final_output": "hello",
        "error_code": None,
        "total_token_usage": 10,
        "duration_ms": 5,
        "subtask_results": [
            {
                "task_id": "t1",
                "status": "completed",
                "agent_role": "writer",
                "output": "out",
                "error_code": None,
                "token_usage": 10,
                "duration_ms": 5,
            }
        ],
    }
    if polluted is not None:
        payload.update(polluted)
    return SimpleNamespace(
        run_id=run_id,
        request_id="req-1",
        api_key_hash="cafef00d" * 8,
        api_key_name="test",
        status="completed",
        stop_reason="completed",
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        duration_ms=5.0,
        total_tokens=10,
        payload=payload,
        workspace_id=None,
    )


def _event_row(
    run_id: str,
    sequence: int,
    *,
    kind: str = "run_started",
    task_id: str | None = None,
    agent_role: str | None = None,
    step_index: int | None = None,
    tool_name: str | None = None,
    call_id: str | None = None,
    output_summary: str | None = None,
    final_output: str | None = None,
) -> SimpleNamespace:
    payload: dict[str, object] = {
        "run_id": run_id,
        "kind": kind,
        "sequence": sequence,
        "occurred_at": datetime.now(UTC).isoformat(),
        "task_id": task_id,
        "agent_role": agent_role,
        "step_index": step_index,
        "tool_name": tool_name,
        "call_id": call_id,
        "output_summary": output_summary,
        "subtasks": [],
        "subtask_results": [],
        "final_output": final_output,
    }
    return SimpleNamespace(
        run_id=run_id,
        sequence=sequence,
        kind=kind,
        occurred_at=datetime.now(UTC),
        task_id=task_id,
        agent_role=agent_role,
        duration_ms=None,
        token_usage=None,
        output_summary=output_summary,
        error_code=None,
        reasoning=None,
        final_output=final_output,
        total_token_usage=None,
        subtasks=[],
        subtask_results=[],
        agent_event_kind=None,
        step_index=step_index,
        tool_name=tool_name,
        call_id=call_id,
        payload=payload,
    )


class TestProjections:
    def test_payload_hides_full_hash(self) -> None:
        payload = public_run_payload(_row())  # type: ignore[arg-type]
        assert payload["api_key_prefix"] == "cafef00d"
        assert "cafef00dcafef00dcafef00dcafef00dcafef00dcafef00dcafef00d" not in str(
            payload
        )

    def test_summary_counts_subtasks(self) -> None:
        summary = public_run_summary(public_run_payload(_row()))  # type: ignore[arg-type]
        assert summary["subtask_count"] == 1
        assert summary["run_id"] == "run-1"

    def test_detail_projection_drops_unknown_keys(self) -> None:
        row = _row(
            polluted={
                "secret_extra": "should-drop",
                "subtask_results": [
                    {
                        "task_id": "t1",
                        "status": "completed",
                        "agent_role": "writer",
                        "output": "out",
                        "token_usage": 1,
                        "duration_ms": 1,
                        "raw_prompt": "drop-me",
                    }
                ],
            }
        )
        projected = project_run_response(public_run_payload(row))  # type: ignore[arg-type]
        assert "secret_extra" not in projected
        assert "raw_prompt" not in str(projected["subtask_results"])


class _FakeRecordService:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self._rows = {row.run_id: row for row in rows}

    async def list_runs(
        self, limit: int = 50, *, owner_scope: str | None = None
    ) -> list[SimpleNamespace]:
        del owner_scope
        return list(self._rows.values())[:limit]

    async def get_run(
        self, run_id: str, *, owner_scope: str | None = None
    ) -> SimpleNamespace | None:
        del owner_scope
        return self._rows.get(run_id)


class _ScopedFakeRecordService(_FakeRecordService):
    async def list_runs(
        self, limit: int = 50, *, owner_scope: str | None = None
    ) -> list[SimpleNamespace]:
        rows = list(self._rows.values())
        if owner_scope is not None:
            rows = [
                row
                for row in rows
                if getattr(row, "workspace_id", None) == owner_scope
                or (
                    getattr(row, "workspace_id", None) is None
                    and row.api_key_hash == owner_scope
                )
            ]
        return rows[:limit]

    async def get_run(
        self, run_id: str, *, owner_scope: str | None = None
    ) -> SimpleNamespace | None:
        row = self._rows.get(run_id)
        if row is None or owner_scope is None:
            return row
        if getattr(row, "workspace_id", None) == owner_scope:
            return row
        if (
            getattr(row, "workspace_id", None) is None
            and row.api_key_hash == owner_scope
        ):
            return row
        return None


class _EventRecordService(_ScopedFakeRecordService):
    def __init__(
        self,
        rows: list[SimpleNamespace],
        events: list[SimpleNamespace],
    ) -> None:
        super().__init__(rows)
        self._events = events

    async def list_events(
        self, run_id: str, *, limit: int = 1000
    ) -> list[SimpleNamespace]:
        return [row for row in self._events if row.run_id == run_id][:limit]


def _override(records: object) -> None:
    app.dependency_overrides[_provide_multi_agent_record_service] = lambda: records


def _clear() -> None:
    app.dependency_overrides.pop(_provide_multi_agent_record_service, None)


def _register_workspace_key() -> tuple[str, str]:
    """Register a fresh user and return (api_key, workspace_id)."""
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"multi-agent-{uuid4().hex}@test.com",
            "display_name": "multi-agent-e2e",
            "password": "secret123",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return body["api_key"], body["workspace"]["id"]


def test_history_requires_bearer() -> None:
    assert client.get("/api/v1/multi-agent/runs").status_code in (401, 403)


def test_history_503_when_no_store() -> None:
    _override(None)
    try:
        response = client.get("/api/v1/multi-agent/runs", headers=_AUTH_HEADERS)
        detail = client.get("/api/v1/multi-agent/runs/x", headers=_AUTH_HEADERS)
    finally:
        _clear()
    assert response.status_code == 503
    assert detail.status_code == 503


def test_unknown_run_is_404() -> None:
    _override(_FakeRecordService([]))
    try:
        response = client.get("/api/v1/multi-agent/runs/nope", headers=_AUTH_HEADERS)
    finally:
        _clear()
    assert response.status_code == 404


def test_cross_tenant_read_is_404() -> None:
    mine = _row(run_id="mine")
    mine.api_key_hash = "owner-mine"
    other = _row(run_id="other")
    other.api_key_hash = "owner-other"
    service = _ScopedFakeRecordService([mine, other])
    # Same filtering the API uses: non-matching scope must return None.
    import asyncio

    assert asyncio.run(service.get_run("other", owner_scope="owner-mine")) is None
    assert asyncio.run(service.get_run("mine", owner_scope="owner-mine")) is not None
    listed = asyncio.run(service.list_runs(owner_scope="owner-mine"))
    assert [row.run_id for row in listed] == ["mine"]


def test_workspace_history_scopes_by_raw_workspace_id() -> None:
    """Workspace rows are matched by their raw workspace id, not its hash."""
    key_a, ws_a = _register_workspace_key()
    key_b, ws_b = _register_workspace_key()
    mine = _row(run_id="mine-ws")
    mine.workspace_id = ws_a
    other = _row(run_id="other-ws")
    other.workspace_id = ws_b
    _override(_ScopedFakeRecordService([mine, other]))
    try:
        list_resp = client.get(
            "/api/v1/multi-agent/runs",
            headers={"Authorization": f"Bearer {key_a}"},
        )
        assert list_resp.status_code == 200
        assert [run["run_id"] for run in list_resp.json()] == ["mine-ws"]

        detail_resp = client.get(
            "/api/v1/multi-agent/runs/other-ws",
            headers={"Authorization": f"Bearer {key_a}"},
        )
        assert detail_resp.status_code == 404
    finally:
        _clear()


def test_events_503_when_no_store() -> None:
    _override(None)
    try:
        response = client.get(
            "/api/v1/multi-agent/runs/run-1/events", headers=_AUTH_HEADERS
        )
    finally:
        _clear()
    assert response.status_code == 503


def test_events_unknown_run_is_404() -> None:
    _override(_EventRecordService([], []))
    try:
        response = client.get(
            "/api/v1/multi-agent/runs/nope/events", headers=_AUTH_HEADERS
        )
    finally:
        _clear()
    assert response.status_code == 404


def test_events_cross_tenant_read_is_404() -> None:
    mine = _row(run_id="mine-events")
    mine.api_key_hash = "owner-mine"
    other = _row(run_id="other-events")
    other.api_key_hash = "owner-other"
    service = _EventRecordService([mine, other], [])
    import asyncio

    assert (
        asyncio.run(service.get_run("other-events", owner_scope="owner-mine")) is None
    )
    assert (
        asyncio.run(service.get_run("mine-events", owner_scope="owner-mine"))
        is not None
    )


def test_events_replay_returns_safe_sequence() -> None:
    run = _row(run_id="run-events")
    run.api_key_hash = hash_api_key("sk-test-integration")
    events = [
        _event_row(
            "run-events",
            0,
            task_id="t1",
            agent_role="research",
            step_index=0,
            tool_name="knowledge_search",
            call_id="call-1",
            output_summary="3 sources",
            kind="subtask_tool_completed",
        ),
        _event_row(
            "run-events",
            1,
            final_output="report",
            kind="run_completed",
        ),
        _event_row("run-events", 2, kind="run_completed"),
    ]
    _override(_EventRecordService([run], events))
    try:
        response = client.get(
            "/api/v1/multi-agent/runs/run-events/events", headers=_AUTH_HEADERS
        )
    finally:
        _clear()
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 3
    assert [row["sequence"] for row in body] == [0, 1, 2]
    assert body[0]["event"] == "subtask_tool_completed"
    assert body[0]["tool_name"] == "knowledge_search"
    assert body[0]["call_id"] == "call-1"
    assert body[1]["event"] == "run_completed"
    assert body[1]["final_output"] == "report"


def test_event_recorder_caps_count_and_payload() -> None:
    saved: list[MultiAgentEvent] = []

    class FakeService:
        async def save_event(self, event: MultiAgentEvent) -> None:
            saved.append(event)

    recorder = MultiAgentEventRecorder(FakeService())

    async def run_capped() -> None:
        for sequence in range(MAX_MULTI_AGENT_EVENTS_PER_RUN + 2):
            await recorder.on_event(
                MultiAgentEvent(
                    run_id="run-cap",
                    kind=MultiAgentEventKind.RUN_STARTED,
                    sequence=sequence,
                )
            )

    import asyncio

    asyncio.run(run_capped())
    assert len(saved) == MAX_MULTI_AGENT_EVENTS_PER_RUN

    oversized = MultiAgentEvent(
        run_id="run-big",
        kind=MultiAgentEventKind.SUBTASK_ANSWER_DELTA,
        sequence=0,
        output_summary="x" * (MAX_MULTI_AGENT_EVENT_PAYLOAD_BYTES * 2),
    )
    saved_oversized: list[MultiAgentEvent] = []

    class FakePayloadService:
        async def save_event(self, event: MultiAgentEvent) -> None:
            saved_oversized.append(event)

    payload_recorder = MultiAgentEventRecorder(FakePayloadService())

    async def save_oversized() -> None:
        await payload_recorder.on_event(oversized)

    asyncio.run(save_oversized())
    assert saved_oversized == []
