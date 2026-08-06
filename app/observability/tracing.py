from __future__ import annotations

import logging
import time

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)
from opentelemetry.trace import Span, Status, StatusCode

from app.core.settings import Settings

logger = logging.getLogger(__name__)

_TRACER_NAME = "ai-platform-mini"
_TRACER_VERSION = "0.1.0"
_provider: TracerProvider | None = None
_noop_provider = trace.NoOpTracerProvider()


def setup_telemetry(
    settings: Settings,
    *,
    span_processor: SpanProcessor | None = None,
) -> None:
    """Install the application tracer provider when telemetry is enabled.

    The optional ``span_processor`` is a test seam for in-memory exporters.
    Disabled mode always resets to the no-op tracer so the application never
    depends on an external collector unless configured.
    """

    global _provider
    _shutdown_provider()

    if not settings.telemetry_enabled:
        _provider = None
        return

    resource = Resource.create({"service.name": settings.telemetry_service_name})
    provider = TracerProvider(resource=resource)
    if span_processor is not None:
        provider.add_span_processor(span_processor)
    elif settings.telemetry_exporter == "console":
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    else:
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=settings.telemetry_otlp_endpoint)
            )
        )
    _provider = provider


def shutdown_telemetry() -> None:
    """Flush and release the installed provider, then reset to no-op."""

    global _provider
    provider = _provider
    _provider = None
    if provider is not None:
        try:
            provider.shutdown()
        except Exception:
            logger.exception("OpenTelemetry provider shutdown failed")


def get_tracer() -> trace.Tracer:
    """Return the application tracer for the installed provider."""

    provider = _provider
    if provider is None:
        return _noop_provider.get_tracer(_TRACER_NAME, _TRACER_VERSION)
    return provider.get_tracer(_TRACER_NAME, _TRACER_VERSION)


def set_span_duration_ms(span: Span, start: float, attribute: str) -> None:
    """Record wall-clock duration on a span without exposing timestamps."""

    span.set_attribute(attribute, round((time.monotonic() - start) * 1000, 2))


def set_span_error(span: Span) -> None:
    """Mark a span failed without attaching exception payloads or stacks."""

    span.set_status(Status(StatusCode.ERROR))


def _shutdown_provider() -> None:
    global _provider
    provider = _provider
    _provider = None
    if provider is not None:
        try:
            provider.shutdown()
        except Exception:
            logger.exception("OpenTelemetry provider shutdown failed")
