"""Optional OpenTelemetry tracing integration."""

from app.observability.context import attach_request_id
from app.observability.metrics import (
    get_meter,
    record_http_request,
    record_llm_call,
    record_llm_tokens,
    record_rag_retrieval,
    record_tool_execution,
    setup_metrics,
    shutdown_metrics,
)
from app.observability.middleware import TelemetryMiddleware
from app.observability.tracing import (
    get_tracer,
    set_span_duration_ms,
    set_span_error,
    setup_telemetry,
    shutdown_telemetry,
)

__all__ = [
    "TelemetryMiddleware",
    "attach_request_id",
    "get_meter",
    "get_tracer",
    "record_http_request",
    "record_llm_call",
    "record_llm_tokens",
    "record_rag_retrieval",
    "record_tool_execution",
    "setup_metrics",
    "set_span_duration_ms",
    "set_span_error",
    "setup_telemetry",
    "shutdown_metrics",
    "shutdown_telemetry",
]
