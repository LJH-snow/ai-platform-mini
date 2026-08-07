from __future__ import annotations

import logging

from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    MetricReader,
    PeriodicExportingMetricReader,
)

from app.core.settings import Settings
from app.observability.tracing import _normalize_otlp_base

logger = logging.getLogger(__name__)

_METER_NAME = "ai-platform-mini"
_METER_VERSION = "0.1.0"
_provider: MeterProvider | None = None
_noop_provider = metrics.NoOpMeterProvider()
_METRICS_PATH = "/v1/metrics"


def setup_metrics(
    settings: Settings,
    *,
    metric_reader: MetricReader | None = None,
) -> None:
    """Install the application meter provider when telemetry is enabled.

    The optional ``metric_reader`` is a test seam for in-memory readers.
    Disabled mode always resets to the no-op meter so the application never
    depends on an external collector unless configured.
    """

    global _provider
    _shutdown_provider()

    if not settings.telemetry_enabled or not settings.telemetry_metrics_enabled:
        _provider = None
        return

    if metric_reader is not None:
        readers: list[MetricReader] = [metric_reader]
    elif settings.telemetry_exporter == "console":
        readers = [
            PeriodicExportingMetricReader(
                ConsoleMetricExporter(), export_interval_millis=5000
            )
        ]
    else:
        readers = [
            PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=_otlp_metrics_endpoint(settings)),
                export_interval_millis=5000,
            )
        ]
    _provider = MeterProvider(metric_readers=readers)


def _otlp_metrics_endpoint(settings: Settings) -> str:
    """Return the OTLP metrics endpoint, normalizing the base first."""

    base = _normalize_otlp_base(settings.telemetry_otlp_endpoint)
    return f"{base}{_METRICS_PATH}"


def shutdown_metrics() -> None:
    """Flush and release the installed provider, then reset to no-op."""

    global _provider
    provider = _provider
    _provider = None
    if provider is not None:
        try:
            provider.shutdown()
        except Exception:
            logger.exception("OpenTelemetry metrics provider shutdown failed")


def get_meter() -> metrics.Meter:
    """Return the application meter for the installed provider."""

    provider = _provider
    if provider is None:
        return _noop_provider.get_meter(_METER_NAME, _METER_VERSION)
    return provider.get_meter(_METER_NAME, _METER_VERSION)


def record_http_request(
    method: str,
    endpoint: str,
    status_code: int,
    duration_ms: float,
) -> None:
    """Record one HTTP request count and duration histogram."""

    meter = get_meter()
    attributes: dict[str, str | int] = {
        "http.request.method": method,
        "http.request.endpoint": endpoint,
        "http.response.status_code": status_code,
    }
    meter.create_counter(
        "http.requests",
        unit="{request}",
        description="Number of HTTP requests processed.",
    ).add(1, attributes)
    meter.create_histogram(
        "http.duration_ms",
        unit="ms",
        description="HTTP request handling duration.",
    ).record(duration_ms, attributes)


def record_llm_call(
    model: str,
    stream: bool,
    status: str,
    duration_ms: float,
) -> None:
    """Record one LLM call count and duration histogram."""

    meter = get_meter()
    attributes: dict[str, str | bool] = {
        "llm.model": model,
        "llm.stream": stream,
        "llm.status": status,
    }
    meter.create_counter(
        "llm.calls",
        unit="{call}",
        description="Number of LLM provider calls.",
    ).add(1, attributes)
    meter.create_histogram(
        "llm.duration_ms",
        unit="ms",
        description="LLM provider call duration.",
    ).record(duration_ms, attributes)


def record_llm_tokens(
    model: str,
    *,
    prompt_tokens: int | None,
    completion_tokens: int | None,
) -> None:
    """Record LLM token usage counters when the provider reports usage."""

    meter = get_meter()
    attributes: dict[str, str] = {"llm.model": model}
    if prompt_tokens is not None:
        meter.create_counter(
            "llm.prompt_tokens",
            unit="{token}",
            description="Number of prompt tokens consumed by LLM calls.",
        ).add(prompt_tokens, attributes)
    if completion_tokens is not None:
        meter.create_counter(
            "llm.completion_tokens",
            unit="{token}",
            description="Number of completion tokens produced by LLM calls.",
        ).add(completion_tokens, attributes)


def record_tool_execution(name: str, status: str, duration_ms: float) -> None:
    """Record one tool execution count and duration histogram."""

    meter = get_meter()
    attributes: dict[str, str] = {
        "tool.name": name,
        "tool.status": status,
    }
    meter.create_counter(
        "tool.executions",
        unit="{call}",
        description="Number of tool executions.",
    ).add(1, attributes)
    meter.create_histogram(
        "tool.duration_ms",
        unit="ms",
        description="Tool execution duration.",
    ).record(duration_ms, attributes)


def record_rag_retrieval(status: str, duration_ms: float) -> None:
    """Record one RAG retrieval count and duration histogram."""

    meter = get_meter()
    attributes: dict[str, str] = {"rag.status": status}
    meter.create_counter(
        "rag.retrievals",
        unit="{call}",
        description="Number of RAG retrieval calls.",
    ).add(1, attributes)
    meter.create_histogram(
        "rag.duration_ms",
        unit="ms",
        description="RAG retrieval duration.",
    ).record(duration_ms, attributes)


def _shutdown_provider() -> None:
    global _provider
    provider = _provider
    _provider = None
    if provider is not None:
        try:
            provider.shutdown()
        except Exception:
            logger.exception("OpenTelemetry metrics provider shutdown failed")
