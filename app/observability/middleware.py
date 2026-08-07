from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterable, AsyncIterator

from opentelemetry import trace
from opentelemetry.trace import Span
from starlette.middleware.base import (
    BaseHTTPMiddleware,
    RequestResponseEndpoint,
)
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from app.core.context import RequestContext
from app.observability.context import reset_request_id, set_request_id
from app.observability.metrics import record_http_request
from app.observability.tracing import (
    get_tracer,
    set_span_duration_ms,
    set_span_error,
)


class TelemetryMiddleware(BaseHTTPMiddleware):
    """Create one HTTP root span that covers streaming responses too."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        tracer = get_tracer()
        start = time.monotonic()
        span = tracer.start_span(f"{request.method} {request.url.path}")
        span.set_attribute("http.request.method", request.method)
        span.set_attribute("http.request.endpoint", request.url.path)
        try:
            with trace.use_span(span, end_on_exit=False):
                response = await call_next(request)
        except asyncio.CancelledError:
            span.set_attribute("http.cancelled", True)
            set_span_duration_ms(span, start, "http.duration_ms")
            record_http_request(
                request.method,
                request.url.path,
                status_code=499,
                duration_ms=round((time.monotonic() - start) * 1000, 2),
            )
            span.end()
            raise
        except BaseException:
            set_span_error(span)
            span.set_attribute("http.response.status_code", 500)
            set_span_duration_ms(span, start, "http.duration_ms")
            record_http_request(
                request.method,
                request.url.path,
                status_code=500,
                duration_ms=round((time.monotonic() - start) * 1000, 2),
            )
            span.end()
            raise

        _attach_request_attributes(span, request)
        span.set_attribute("http.response.status_code", response.status_code)
        if isinstance(response, StreamingResponse):
            return _wrap_streaming_response(
                response,
                span=span,
                request=request,
                start=start,
            )

        set_span_duration_ms(span, start, "http.duration_ms")
        record_http_request(
            request.method,
            request.url.path,
            status_code=response.status_code,
            duration_ms=round((time.monotonic() - start) * 1000, 2),
        )
        span.end()
        return response


def _wrap_streaming_response(
    response: StreamingResponse,
    *,
    span: Span,
    request: Request,
    start: float,
) -> StreamingResponse:
    response.body_iterator = _instrument_stream(
        response.body_iterator,
        span=span,
        request=request,
        start=start,
        status_code=response.status_code,
    )
    return response


async def _instrument_stream(
    iterable: AsyncIterable[bytes | str | memoryview[int]],
    *,
    span: Span,
    request: Request,
    start: float,
    status_code: int,
) -> AsyncIterator[bytes | str | memoryview[int]]:
    context: RequestContext | None = getattr(request.state, "context", None)
    token = set_request_id(context.request_id if context is not None else None)
    effective_status = status_code
    effective_error = False
    try:
        iterator = iterable.__aiter__()
        with trace.use_span(span, end_on_exit=False):
            async for chunk in iterator:
                yield chunk
    except asyncio.CancelledError:
        span.set_attribute("http.cancelled", True)
        effective_status = 499
        raise
    except Exception:
        effective_error = True
        effective_status = 500
        raise
    finally:
        _attach_request_attributes(span, request)
        if effective_error:
            set_span_error(span)
        span.set_attribute("http.response.status_code", effective_status)
        set_span_duration_ms(span, start, "http.duration_ms")
        record_http_request(
            request.method,
            request.url.path,
            status_code=effective_status,
            duration_ms=round((time.monotonic() - start) * 1000, 2),
        )
        span.end()
        reset_request_id(token)


def _attach_request_attributes(span: Span, request: Request) -> None:
    context: RequestContext | None = getattr(request.state, "context", None)
    if context is None:
        return
    span.set_attribute("app.request_id", context.request_id)
    if context.api_key is not None:
        span.set_attribute("app.api_key_hash", _sanitize_key_hash(context.api_key))


def _sanitize_key_hash(key_hash: str) -> str:
    """Expose only a stable prefix of the already-hashed API key."""

    return key_hash[:8] if len(key_hash) > 8 else key_hash
