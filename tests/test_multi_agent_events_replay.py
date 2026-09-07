"""PostgreSQL integration tests for multi-agent event persistence."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest

from app.db.init import dispose_db, init_db
from app.db.session import create_async_session_factory
from app.multi_agent.events import MultiAgentEvent, MultiAgentEventKind
from app.services.multi_agent_run_record_service import MultiAgentRunRecordService

_SKIP_REASON = "Set INTEGRATION_TEST=1 to run PostgreSQL integration tests"

pytestmark = pytest.mark.skipif(
    not os.getenv("INTEGRATION_TEST"),
    reason=_SKIP_REASON,
)


@pytest.fixture()
async def record_service() -> AsyncGenerator[MultiAgentRunRecordService, None]:
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as pg:
        database_url = pg.get_connection_url().replace("psycopg2", "asyncpg")
        await init_db(database_url)
        factory = create_async_session_factory()
        yield MultiAgentRunRecordService(factory)
        await dispose_db()


@pytest.mark.asyncio
async def test_event_roundtrip_preserves_step_tool_fields(
    record_service: MultiAgentRunRecordService,
) -> None:
    await record_service.save_event(
        MultiAgentEvent(
            run_id="run-events-integration",
            kind=MultiAgentEventKind.SUBTASK_TOOL_COMPLETED,
            sequence=0,
            occurred_at=datetime.now(UTC),
            task_id="t1",
            agent_role="research",
            step_index=0,
            tool_name="knowledge_search",
            call_id="call-1",
            output_summary="3 sources",
        )
    )
    rows = await record_service.list_events("run-events-integration")
    assert len(rows) == 1
    row = rows[0]
    assert row.kind == "subtask_tool_completed"
    assert row.task_id == "t1"
    assert row.step_index == 0
    assert row.tool_name == "knowledge_search"
    assert row.call_id == "call-1"
    assert row.output_summary == "3 sources"
    assert row.payload["tool_name"] == "knowledge_search"


@pytest.mark.asyncio
async def test_event_list_orders_by_sequence(
    record_service: MultiAgentRunRecordService,
) -> None:
    await record_service.save_event(
        MultiAgentEvent(
            run_id="run-order",
            kind=MultiAgentEventKind.RUN_STARTED,
            sequence=0,
            occurred_at=datetime.now(UTC),
        )
    )
    await record_service.save_event(
        MultiAgentEvent(
            run_id="run-order",
            kind=MultiAgentEventKind.RUN_COMPLETED,
            sequence=1,
            occurred_at=datetime.now(UTC),
            final_output="done",
        )
    )
    rows = await record_service.list_events("run-order")
    assert [row.sequence for row in rows] == [0, 1]
    assert rows[-1].final_output == "done"
