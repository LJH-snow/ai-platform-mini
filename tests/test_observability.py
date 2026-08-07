from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.testclient import TestClient
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.metrics._internal.point import (
    HistogramDataPoint,
    NumberDataPoint,
)
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import NonRecordingSpan, SpanContext, StatusCode, TraceFlags

from app.agents import AgentModel, AgentRuntime, AgentTool
from app.agents.models import (
    AgentEvent,
    AgentEventKind,
    AgentRunResult,
    AgentState,
    RunStatus,
    StopReason,
)
from app.auth.models import APIKey
from app.core.context import RequestContext
from app.core.settings import Settings
from app.middleware.context import ContextMiddleware
from app.observability import (
    TelemetryMiddleware,
    get_tracer,
    setup_metrics,
    setup_telemetry,
    shutdown_metrics,
    shutdown_telemetry,
)
from app.providers.mock import MockProvider
from app.quota.models import QuotaConfig
from app.quota.repository import QuotaRepository
from app.quota.service import QuotaService
from app.rag.service import RAGService
from app.rag.vector_store import SearchResult, VectorStore
from app.runs.protocols import RunTraceRecorderFactory
from app.schemas.agent import AgentRunRequest
from app.schemas.chat import ChatRequest
from app.services.agent_service import AgentService
from app.services.chat_service import ChatService
from app.tools.executor import ToolExecutor
from app.tools.models import RiskLevel, ToolContext
from app.tools.registry import ToolRegistry
from app.usage.collector import UsageCollector
from app.usage.memory_repository import InMemoryUsageRepository
from app.usage.service import UsageService


class TokenMockProvider(MockProvider):
    async def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = await super().chat(payload)
        data["prompt_eval_count"] = 12
        data["eval_count"] = 7
        return data

    async def chat_stream(
        self, payload: dict[str, Any]
    ) -> AsyncIterator[dict[str, Any]]:
        async for chunk in super().chat_stream(payload):
            if chunk["done"] is True:
                chunk["prompt_eval_count"] = 12
                chunk["eval_count"] = 7
            yield chunk


class RaisingProvider(MockProvider):
    async def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        del payload
        raise RuntimeError("provider failure")


class CancellingProvider(MockProvider):
    async def chat_stream(
        self, payload: dict[str, Any]
    ) -> AsyncIterator[dict[str, Any]]:
        async for chunk in super().chat_stream(payload):
            yield chunk
            raise asyncio.CancelledError()


@dataclass
class EchoTool:
    name: str = "echo"
    description: str = "Echo a message."
    input_schema: Mapping[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
            "additionalProperties": False,
        }
    )
    output_schema: Mapping[str, object] = field(
        default_factory=lambda: {"type": "string"}
    )
    risk_level: RiskLevel = RiskLevel.MEDIUM
    required_permissions: tuple[str, ...] = ()

    async def execute(
        self,
        arguments: Mapping[str, object],
        context: ToolContext,
    ) -> str:
        del context
        return str(arguments["message"])


class FakeEmbedder:
    async def embed_query(self, text: str) -> list[float]:
        del text
        return [0.1]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        del texts
        return [[0.1]]

    async def close(self) -> None:
        return None


class FakeVectorStore:
    async def search(
        self,
        query_embedding: list[float],
        top_k: int,
        *,
        owner_key_hash: str | None = None,
        query: str | None = None,
    ) -> list[SearchResult]:
        del query_embedding, top_k, owner_key_hash, query
        return [
            SearchResult(
                document_id="document-1",
                chunk_id="chunk-1",
                chunk_index=0,
                content="safe chunk one",
                distance=0.1,
            ),
            SearchResult(
                document_id="document-2",
                chunk_id="chunk-2",
                chunk_index=0,
                content="safe chunk two",
                distance=0.2,
            ),
            SearchResult(
                document_id="document-3",
                chunk_id="chunk-3",
                chunk_index=0,
                content="safe chunk three",
                distance=0.5,
            ),
        ]


class _FakeRuntime:
    def __init__(self, result: AgentRunResult) -> None:
        self._result = result

    async def run(
        self,
        user_input: str,
        *,
        max_steps: int = 8,
        timeout: float | None = None,
        deadline: float | None = None,
        cancel_event: asyncio.Event | None = None,
        token_budget: int | None = None,
        run_id: str | None = None,
        request_id: str | None = None,
        model: str | None = None,
        stream_answer: bool = False,
        tool_context_metadata: Mapping[str, object] | None = None,
    ) -> AgentRunResult:
        del user_input, max_steps, timeout, deadline, cancel_event, token_budget
        del run_id, request_id, model, stream_answer, tool_context_metadata
        return self._result


class _FakeRuntimeFactory:
    def __init__(self, result: AgentRunResult) -> None:
        self._result = result

    def __call__(
        self,
        model: AgentModel,
        tools: Mapping[str, AgentTool] | None,
        *,
        tool_executor: ToolExecutor | None = None,
        recorder_factory: RunTraceRecorderFactory | None = None,
    ) -> AgentRuntime:
        del model, tools, tool_executor, recorder_factory
        return cast(AgentRuntime, _FakeRuntime(self._result))


@pytest.fixture
def exporter() -> Iterator[InMemorySpanExporter]:
    memory_exporter = InMemorySpanExporter()
    yield memory_exporter
    memory_exporter.clear()
    shutdown_telemetry()


@pytest.fixture
def metric_reader() -> Iterator[InMemoryMetricReader]:
    reader = InMemoryMetricReader()
    yield reader
    shutdown_metrics()


def _enable(exporter: InMemorySpanExporter) -> None:
    setup_telemetry(
        Settings(telemetry_enabled=True),
        span_processor=SimpleSpanProcessor(exporter),
    )


def _enable_metrics(reader: InMemoryMetricReader) -> None:
    setup_metrics(Settings(telemetry_enabled=True), metric_reader=reader)


def _attributes(span: ReadableSpan) -> Mapping[str, object]:
    attributes = span.attributes
    assert attributes is not None
    return cast(Mapping[str, object], attributes)


def _metric_values(
    reader: InMemoryMetricReader,
) -> dict[str, list[tuple[Mapping[str, object], int | float]]]:
    data = reader.get_metrics_data()
    assert data is not None
    result: dict[str, list[tuple[Mapping[str, object], int | float]]] = {}
    for resource_metric in data.resource_metrics:
        for scope_metric in resource_metric.scope_metrics:
            for metric in scope_metric.metrics:
                points: list[tuple[Mapping[str, object], int | float]] = []
                for point in metric.data.data_points:
                    if isinstance(point, HistogramDataPoint):
                        value: int | float = point.sum
                    else:
                        assert isinstance(point, NumberDataPoint)
                        value = point.value
                    attribute_map = (
                        dict(point.attributes) if point.attributes is not None else {}
                    )
                    points.append((attribute_map, value))
                result[metric.name] = points
    return result


def _agent_result() -> AgentRunResult:
    run_id = "run-otel-1"
    occurred_at = datetime(2026, 8, 7, tzinfo=UTC)
    state = AgentState(run_id=run_id, user_input="hello")
    events = (
        AgentEvent(
            kind=AgentEventKind.RUN_STARTED,
            run_id=run_id,
            sequence=1,
            occurred_at=occurred_at,
        ),
        AgentEvent(
            kind=AgentEventKind.RUN_STOPPED,
            run_id=run_id,
            sequence=2,
            occurred_at=occurred_at,
            status=RunStatus.COMPLETED,
            stop_reason=StopReason.DIRECT_ANSWER,
        ),
    )
    return AgentRunResult(
        run_id=run_id,
        status=RunStatus.COMPLETED,
        stop_reason=StopReason.DIRECT_ANSWER,
        answer="hello",
        state=state,
        events=events,
        token_usage=19,
    )


def test_disabled_mode_does_not_export_spans(exporter: InMemorySpanExporter) -> None:
    setup_telemetry(
        Settings(telemetry_enabled=False),
        span_processor=SimpleSpanProcessor(exporter),
    )

    tracer = get_tracer()
    with tracer.start_as_current_span("manual.span"):
        pass

    assert exporter.get_finished_spans() == ()


@pytest.mark.asyncio
async def test_llm_chat_span_records_model_duration_and_tokens(
    exporter: InMemorySpanExporter,
) -> None:
    _enable(exporter)
    service = ChatService(provider=TokenMockProvider())

    response = await service.chat(ChatRequest(message="Hi"))

    assert response.prompt_tokens == 12
    spans = exporter.get_finished_spans()
    assert [span.name for span in spans] == ["llm.chat"]
    attributes = _attributes(spans[0])
    assert attributes["llm.model"] == "mock-model"
    assert attributes["llm.stream"] is False
    assert attributes["llm.usage.prompt_tokens"] == 12
    assert attributes["llm.usage.completion_tokens"] == 7
    assert "llm.duration_ms" in attributes


@pytest.mark.asyncio
async def test_llm_chat_error_marks_span_without_stack(
    exporter: InMemorySpanExporter,
) -> None:
    _enable(exporter)
    service = ChatService(provider=RaisingProvider())

    with pytest.raises(RuntimeError, match="provider failure"):
        await service.chat(ChatRequest(message="Hi"))

    spans = exporter.get_finished_spans()
    assert [span.name for span in spans] == ["llm.chat"]
    span = spans[0]
    assert span.status.status_code == StatusCode.ERROR
    attributes = _attributes(span)
    assert "llm.duration_ms" in attributes
    assert "exception" not in attributes


@pytest.mark.asyncio
async def test_llm_span_does_not_expose_prompt(
    exporter: InMemorySpanExporter,
) -> None:
    _enable(exporter)
    service = ChatService(provider=TokenMockProvider())

    await service.chat(ChatRequest(message="secret-prompt-value"))

    attributes = _attributes(exporter.get_finished_spans()[0])
    serialized = repr(attributes)
    assert "secret-prompt-value" not in serialized
    assert "llm.prompt" not in attributes
    assert "llm.messages" not in attributes
    assert "llm.usage.prompt_tokens" in attributes
    assert "exception" not in attributes


@pytest.mark.asyncio
async def test_llm_chat_stream_span_records_usage(
    exporter: InMemorySpanExporter,
) -> None:
    _enable(exporter)
    service = ChatService(provider=TokenMockProvider())

    chunks = [chunk async for chunk in service.chat_stream(ChatRequest(message="Hi"))]

    assert chunks
    spans = exporter.get_finished_spans()
    assert [span.name for span in spans] == ["llm.chat_stream"]
    attributes = _attributes(spans[0])
    assert attributes["llm.model"] == "mock-model"
    assert attributes["llm.stream"] is True
    assert attributes["llm.usage.prompt_tokens"] == 12
    assert attributes["llm.usage.completion_tokens"] == 7
    assert "llm.duration_ms" in attributes


@pytest.mark.asyncio
async def test_llm_chat_stream_cancel_is_not_error(
    exporter: InMemorySpanExporter,
) -> None:
    _enable(exporter)
    service = ChatService(provider=CancellingProvider())

    with pytest.raises(asyncio.CancelledError):
        async for _ in service.chat_stream(ChatRequest(message="Hi")):
            pass

    spans = exporter.get_finished_spans()
    assert [span.name for span in spans] == ["llm.chat_stream"]
    span = spans[0]
    assert span.status.status_code != StatusCode.ERROR
    attributes = _attributes(span)
    assert attributes["llm.cancelled"] is True
    assert "llm.duration_ms" in attributes


@pytest.mark.asyncio
async def test_tool_execution_span_records_risk_and_status(
    exporter: InMemorySpanExporter,
) -> None:
    _enable(exporter)
    executor = ToolExecutor(
        ToolRegistry([EchoTool()]),
        max_risk_level=RiskLevel.HIGH,
    )

    result = await executor.execute(
        "echo",
        {"message": "hello"},
        ToolContext(run_id="run-1", step_index=1),
    )

    assert result.succeeded is True
    spans = exporter.get_finished_spans()
    assert [span.name for span in spans] == ["tool.execute"]
    attributes = _attributes(spans[0])
    assert attributes["tool.name"] == "echo"
    assert attributes["tool.risk_level"] == "medium"
    assert attributes["tool.status"] == "succeeded"
    assert "tool.duration_ms" in attributes


@pytest.mark.asyncio
async def test_rag_retrieve_span_records_top_k_and_counts(
    exporter: InMemorySpanExporter,
) -> None:
    _enable(exporter)
    service = RAGService(
        embedder=FakeEmbedder(),
        vector_store=cast(VectorStore, FakeVectorStore()),
        chat_service=cast(ChatService, MagicMock()),
        top_k=3,
    )

    prepared = await service.prepare(
        ChatRequest(message="hello"),
        owner_key_hash="a" * 64,
    )

    assert prepared.references
    spans = exporter.get_finished_spans()
    assert [span.name for span in spans] == ["rag.retrieve"]
    attributes = _attributes(spans[0])
    assert attributes["rag.top_k"] == 3
    assert attributes["rag.retrieved_count"] == 3
    assert attributes["rag.used_count"] == 2
    assert "rag.duration_ms" in attributes


@pytest.mark.asyncio
async def test_agent_run_span_records_terminal_metadata(
    exporter: InMemorySpanExporter,
) -> None:
    _enable(exporter)
    usage_repository = InMemoryUsageRepository()
    quota_service = QuotaService(
        usage_repository=usage_repository,
        quota_repository=cast(QuotaRepository, MagicMock()),
        config=QuotaConfig(),
    )
    service = AgentService(
        chat_service=cast(ChatService, MagicMock()),
        quota_service=quota_service,
        usage_collector=UsageCollector(UsageService(InMemoryUsageRepository())),
        runtime_factory=_FakeRuntimeFactory(_agent_result()),
    )

    outcome = await service.run(
        AgentRunRequest(message="hello", model="test-model"),
        context=RequestContext(request_id="req-otel-1", api_key="hashed"),
        api_key=APIKey(key="hashed", name="test"),
    )

    assert outcome.result.run_id == "run-otel-1"
    spans = exporter.get_finished_spans()
    assert [span.name for span in spans] == ["agent.run"]
    attributes = _attributes(spans[0])
    assert attributes["agent.request_id"] == "req-otel-1"
    assert attributes["agent.run_id"] == "run-otel-1"
    assert attributes["agent.stop_reason"] == "direct_answer"
    assert attributes["agent.total_tokens"] == 0
    assert attributes["agent.model"] == "test-model"
    assert "agent.duration_ms" in attributes


def test_http_root_span_records_request_and_key_prefix(
    exporter: InMemorySpanExporter,
) -> None:
    _enable(exporter)
    app = FastAPI()
    app.add_middleware(ContextMiddleware)
    app.add_middleware(TelemetryMiddleware)

    @app.get("/api/v1/ping")
    async def ping(request: Request) -> JSONResponse:
        request.state.context = RequestContext(
            request_id="req-http-1",
            api_key="a" * 64,
            api_key_name="test",
        )
        return JSONResponse({"ok": True})

    with TestClient(app) as client:
        response = client.get("/api/v1/ping")
        assert response.status_code == 200

    spans = exporter.get_finished_spans()
    assert [span.name for span in spans] == ["GET /api/v1/ping"]
    attributes = _attributes(spans[0])
    assert attributes["app.request_id"] == "req-http-1"
    assert attributes["app.api_key_hash"] == "a" * 8
    assert "a" * 64 not in str(attributes)
    assert attributes["http.request.method"] == "GET"
    assert attributes["http.request.endpoint"] == "/api/v1/ping"
    assert attributes["http.response.status_code"] == 200
    assert "http.duration_ms" in attributes


def test_http_error_span_records_duration_and_error(
    exporter: InMemorySpanExporter,
) -> None:
    _enable(exporter)
    app = FastAPI()
    app.add_middleware(TelemetryMiddleware)

    @app.get("/boom")
    async def boom() -> JSONResponse:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        with TestClient(app, raise_server_exceptions=True) as client:
            client.get("/boom")

    spans = exporter.get_finished_spans()
    assert [span.name for span in spans] == ["GET /boom"]
    span = spans[0]
    assert span.status.status_code == StatusCode.ERROR
    attributes = _attributes(span)
    assert attributes["http.response.status_code"] == 500
    assert "http.duration_ms" in attributes


def test_http_root_span_covers_streaming_response(
    exporter: InMemorySpanExporter,
) -> None:
    _enable(exporter)
    app = FastAPI()
    app.add_middleware(ContextMiddleware)
    app.add_middleware(TelemetryMiddleware)

    @app.get("/api/v1/stream")
    async def stream() -> StreamingResponse:
        async def body() -> AsyncIterator[bytes]:
            yield b"one"
            yield b"two"

        return StreamingResponse(body())

    with TestClient(app) as client:
        response = client.get("/api/v1/stream")
        assert response.content == b"onetwo"

    spans = exporter.get_finished_spans()
    assert [span.name for span in spans] == ["GET /api/v1/stream"]
    attributes = _attributes(spans[0])
    assert attributes["http.response.status_code"] == 200
    assert "http.duration_ms" in attributes


def test_sampling_ratio_zero_does_not_export_spans(
    exporter: InMemorySpanExporter,
) -> None:
    setup_telemetry(
        Settings(telemetry_enabled=True, telemetry_sampling_ratio=0.0),
        span_processor=SimpleSpanProcessor(exporter),
    )

    tracer = get_tracer()
    with tracer.start_as_current_span("manual.span"):
        pass

    assert exporter.get_finished_spans() == ()


def test_sampling_ratio_zero_keeps_children_of_sampled_parent(
    exporter: InMemorySpanExporter,
) -> None:
    setup_telemetry(
        Settings(telemetry_enabled=True, telemetry_sampling_ratio=0.0),
        span_processor=SimpleSpanProcessor(exporter),
    )

    parent = NonRecordingSpan(
        SpanContext(
            trace_id=0x1234567890ABCDEF1234567890ABCDEF,
            span_id=0x1234567890ABCDEF,
            is_remote=False,
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
        )
    )
    tracer = get_tracer()
    with otel_trace.use_span(parent):
        with tracer.start_as_current_span("child.span"):
            pass

    spans = exporter.get_finished_spans()
    assert [span.name for span in spans] == ["child.span"]


def test_sampling_ratio_rejects_out_of_range() -> None:
    with pytest.raises(ValueError, match="telemetry_sampling_ratio"):
        Settings(telemetry_sampling_ratio=1.5)
    with pytest.raises(ValueError, match="telemetry_sampling_ratio"):
        Settings(telemetry_sampling_ratio=-0.1)


def test_http_metrics_record_request_count_and_duration(
    exporter: InMemorySpanExporter,
    metric_reader: InMemoryMetricReader,
) -> None:
    _enable(exporter)
    _enable_metrics(metric_reader)
    app = FastAPI()
    app.add_middleware(ContextMiddleware)
    app.add_middleware(TelemetryMiddleware)

    @app.get("/api/v1/ping")
    async def ping() -> JSONResponse:
        return JSONResponse({"ok": True})

    with TestClient(app) as client:
        response = client.get("/api/v1/ping")
        assert response.status_code == 200

    metrics = _metric_values(metric_reader)
    requests = metrics["http.requests"]
    assert len(requests) == 1
    attributes = requests[0][0]
    assert attributes["http.request.method"] == "GET"
    assert attributes["http.request.endpoint"] == "/api/v1/ping"
    assert attributes["http.response.status_code"] == 200
    assert metrics["http.duration_ms"][0][1] > 0


@pytest.mark.asyncio
async def test_llm_metrics_record_calls_and_tokens(
    metric_reader: InMemoryMetricReader,
) -> None:
    _enable_metrics(metric_reader)
    service = ChatService(provider=TokenMockProvider())

    await service.chat(ChatRequest(message="Hi"))

    metrics = _metric_values(metric_reader)
    calls = metrics["llm.calls"]
    assert len(calls) == 1
    attributes = calls[0][0]
    assert attributes["llm.model"] == "mock-model"
    assert attributes["llm.stream"] is False
    assert attributes["llm.status"] == "ok"
    assert metrics["llm.prompt_tokens"][0][1] == 12
    assert metrics["llm.completion_tokens"][0][1] == 7
    assert metrics["llm.duration_ms"][0][1] > 0


@pytest.mark.asyncio
async def test_llm_metrics_record_error_status(
    metric_reader: InMemoryMetricReader,
) -> None:
    _enable_metrics(metric_reader)
    service = ChatService(provider=RaisingProvider())

    with pytest.raises(RuntimeError, match="provider failure"):
        await service.chat(ChatRequest(message="Hi"))

    metrics = _metric_values(metric_reader)
    assert metrics["llm.calls"][0][0]["llm.status"] == "error"
    assert metrics["llm.duration_ms"][0][1] > 0


@pytest.mark.asyncio
async def test_tool_metrics_record_executions(
    metric_reader: InMemoryMetricReader,
) -> None:
    _enable_metrics(metric_reader)
    executor = ToolExecutor(
        ToolRegistry([EchoTool()]),
        max_risk_level=RiskLevel.HIGH,
    )

    result = await executor.execute(
        "echo",
        {"message": "hello"},
        ToolContext(run_id="run-1", step_index=1),
    )

    assert result.succeeded is True
    metrics = _metric_values(metric_reader)
    executions = metrics["tool.executions"]
    assert len(executions) == 1
    attributes = executions[0][0]
    assert attributes["tool.name"] == "echo"
    assert attributes["tool.status"] == "succeeded"
    assert metrics["tool.duration_ms"][0][1] > 0


@pytest.mark.asyncio
async def test_rag_metrics_record_retrievals(
    metric_reader: InMemoryMetricReader,
) -> None:
    _enable_metrics(metric_reader)
    service = RAGService(
        embedder=FakeEmbedder(),
        vector_store=cast(VectorStore, FakeVectorStore()),
        chat_service=cast(ChatService, MagicMock()),
        top_k=3,
    )

    prepared = await service.prepare(
        ChatRequest(message="hello"),
        owner_key_hash="a" * 64,
    )

    assert prepared.references
    metrics = _metric_values(metric_reader)
    retrievals = metrics["rag.retrievals"]
    assert len(retrievals) == 1
    assert retrievals[0][0]["rag.status"] == "ok"
    assert metrics["rag.duration_ms"][0][1] > 0


@pytest.mark.asyncio
async def test_metrics_do_not_expose_sensitive_fields(
    metric_reader: InMemoryMetricReader,
) -> None:
    _enable_metrics(metric_reader)
    service = ChatService(provider=TokenMockProvider())

    await service.chat(ChatRequest(message="secret-prompt-value"))

    serialized = repr(_metric_values(metric_reader))
    assert "secret-prompt-value" not in serialized


@pytest.mark.asyncio
async def test_disabled_mode_does_not_record_metrics(
    metric_reader: InMemoryMetricReader,
) -> None:
    setup_metrics(Settings(telemetry_enabled=False), metric_reader=metric_reader)
    service = ChatService(provider=TokenMockProvider())

    await service.chat(ChatRequest(message="Hi"))

    assert metric_reader.get_metrics_data() is None


@pytest.mark.asyncio
async def test_metrics_disabled_keeps_traces(
    exporter: InMemorySpanExporter,
    metric_reader: InMemoryMetricReader,
) -> None:
    _enable(exporter)
    setup_metrics(
        Settings(telemetry_enabled=True, telemetry_metrics_enabled=False),
        metric_reader=metric_reader,
    )
    service = ChatService(provider=TokenMockProvider())

    await service.chat(ChatRequest(message="Hi"))

    assert [span.name for span in exporter.get_finished_spans()] == ["llm.chat"]
    assert metric_reader.get_metrics_data() is None


def test_request_id_correlates_http_llm_and_tool_spans(
    exporter: InMemorySpanExporter,
) -> None:
    _enable(exporter)
    app = FastAPI()
    app.add_middleware(ContextMiddleware)
    app.add_middleware(TelemetryMiddleware)

    @app.get("/api/v1/chat")
    async def chat() -> JSONResponse:
        service = ChatService(provider=TokenMockProvider())
        await service.chat(ChatRequest(message="hello"))
        executor = ToolExecutor(
            ToolRegistry([EchoTool()]),
            max_risk_level=RiskLevel.HIGH,
        )
        await executor.execute(
            "echo",
            {"message": "hello"},
            ToolContext(run_id="run-1", step_index=1),
        )
        return JSONResponse({"ok": True})

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/chat",
            headers={"X-Request-ID": "req-correlated-1"},
        )
        assert response.status_code == 200

    spans = exporter.get_finished_spans()
    assert {span.name for span in spans} == {
        "GET /api/v1/chat",
        "llm.chat",
        "tool.execute",
    }
    for span in spans:
        assert _attributes(span)["app.request_id"] == "req-correlated-1"


def test_request_id_correlates_streaming_llm_span(
    exporter: InMemorySpanExporter,
) -> None:
    _enable(exporter)
    app = FastAPI()
    app.add_middleware(ContextMiddleware)
    app.add_middleware(TelemetryMiddleware)

    @app.get("/api/v1/stream-chat")
    async def stream_chat() -> StreamingResponse:
        service = ChatService(provider=TokenMockProvider())

        async def body() -> AsyncIterator[bytes]:
            async for _chunk in service.chat_stream(ChatRequest(message="hello")):
                yield b"chunk"

        return StreamingResponse(body())

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/stream-chat",
            headers={"X-Request-ID": "req-stream-1"},
        )
        assert response.status_code == 200
        assert b"chunk" in response.content

    spans = exporter.get_finished_spans()
    assert {span.name for span in spans} == {
        "GET /api/v1/stream-chat",
        "llm.chat_stream",
    }
    for span in spans:
        assert _attributes(span)["app.request_id"] == "req-stream-1"


def test_request_id_correlates_rag_span(
    exporter: InMemorySpanExporter,
) -> None:
    _enable(exporter)
    app = FastAPI()
    app.add_middleware(ContextMiddleware)
    app.add_middleware(TelemetryMiddleware)

    @app.get("/api/v1/rag-query")
    async def rag_query(request: Request) -> JSONResponse:
        service = RAGService(
            embedder=FakeEmbedder(),
            vector_store=cast(VectorStore, FakeVectorStore()),
            chat_service=cast(ChatService, MagicMock()),
            top_k=3,
        )
        prepared = await service.prepare(
            ChatRequest(message="hello"),
            owner_key_hash="a" * 64,
        )
        assert prepared.references
        return JSONResponse({"ok": True})

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/rag-query",
            headers={"X-Request-ID": "req-rag-1"},
        )
        assert response.status_code == 200

    spans = exporter.get_finished_spans()
    assert {span.name for span in spans} == {
        "GET /api/v1/rag-query",
        "rag.retrieve",
    }
    for span in spans:
        assert _attributes(span)["app.request_id"] == "req-rag-1"


def test_request_id_correlates_agent_span(
    exporter: InMemorySpanExporter,
) -> None:
    _enable(exporter)
    app = FastAPI()
    app.add_middleware(ContextMiddleware)
    app.add_middleware(TelemetryMiddleware)
    usage_repository = InMemoryUsageRepository()
    quota_service = QuotaService(
        usage_repository=usage_repository,
        quota_repository=cast(QuotaRepository, MagicMock()),
        config=QuotaConfig(),
    )
    agent_service = AgentService(
        chat_service=cast(ChatService, MagicMock()),
        quota_service=quota_service,
        usage_collector=UsageCollector(UsageService(InMemoryUsageRepository())),
        runtime_factory=_FakeRuntimeFactory(_agent_result()),
    )

    @app.post("/api/v1/agent")
    async def agent_run(request: Request) -> JSONResponse:
        context: RequestContext = request.state.context
        outcome = await agent_service.run(
            AgentRunRequest(message="hello", model="test-model"),
            context=context,
            api_key=APIKey(key="hashed", name="test"),
        )
        assert outcome.result.run_id == "run-otel-1"
        return JSONResponse({"ok": True})

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/agent",
            headers={"X-Request-ID": "req-agent-1"},
        )
        assert response.status_code == 200

    spans = exporter.get_finished_spans()
    assert {span.name for span in spans} == {
        "POST /api/v1/agent",
        "agent.run",
    }
    for span in spans:
        assert _attributes(span)["app.request_id"] == "req-agent-1"
    assert _attributes(spans[0])["app.request_id"] == "req-agent-1"


# ── OTLP endpoint construction ──────────────────────────────────────────


def test_otlp_traces_endpoint_plain_base() -> None:
    from app.observability.tracing import _otlp_traces_endpoint

    settings = _make_telemetry_settings(otlp_endpoint="http://localhost:4318")
    assert _otlp_traces_endpoint(settings) == "http://localhost:4318/v1/traces"


def test_otlp_traces_endpoint_already_has_traces_path() -> None:
    from app.observability.tracing import _otlp_traces_endpoint

    settings = _make_telemetry_settings(otlp_endpoint="http://localhost:4318/v1/traces")
    assert _otlp_traces_endpoint(settings) == "http://localhost:4318/v1/traces"


def test_otlp_traces_endpoint_has_metrics_path() -> None:
    from app.observability.tracing import _otlp_traces_endpoint

    settings = _make_telemetry_settings(
        otlp_endpoint="http://localhost:4318/v1/metrics"
    )
    assert _otlp_traces_endpoint(settings) == "http://localhost:4318/v1/traces"


def test_otlp_metrics_endpoint_plain_base() -> None:
    from app.observability.metrics import _otlp_metrics_endpoint

    settings = _make_telemetry_settings(otlp_endpoint="http://localhost:4318")
    assert _otlp_metrics_endpoint(settings) == "http://localhost:4318/v1/metrics"


def test_otlp_metrics_endpoint_already_has_metrics_path() -> None:
    from app.observability.metrics import _otlp_metrics_endpoint

    settings = _make_telemetry_settings(
        otlp_endpoint="http://localhost:4318/v1/metrics"
    )
    assert _otlp_metrics_endpoint(settings) == "http://localhost:4318/v1/metrics"


def test_otlp_metrics_endpoint_has_traces_path() -> None:
    from app.observability.metrics import _otlp_metrics_endpoint

    settings = _make_telemetry_settings(otlp_endpoint="http://localhost:4318/v1/traces")
    assert _otlp_metrics_endpoint(settings) == "http://localhost:4318/v1/metrics"


def _make_telemetry_settings(otlp_endpoint: str) -> Settings:
    return Settings(
        telemetry_enabled=True,
        telemetry_otlp_endpoint=otlp_endpoint,  # type: ignore[call-arg]
    )
