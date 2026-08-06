"""Optional OpenTelemetry tracing integration."""

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
    "get_tracer",
    "set_span_duration_ms",
    "set_span_error",
    "setup_telemetry",
    "shutdown_telemetry",
]
