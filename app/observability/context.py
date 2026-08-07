from __future__ import annotations

from contextvars import ContextVar, Token

from opentelemetry.trace import Span

_request_id: ContextVar[str | None] = ContextVar("otel_request_id", default=None)


def set_request_id(request_id: str | None) -> Token[str | None]:
    """Bind the current request id for the active async context."""

    return _request_id.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    """Restore the previous request id binding."""

    _request_id.reset(token)


def attach_request_id(span: Span) -> None:
    """Attach the active request id to a span when one is available.

    Span context already nests child spans under the HTTP root span; the
    explicit attribute makes correlation robust for detached or background
    work and for metrics-only deployments.
    """

    request_id = _request_id.get()
    if request_id is not None:
        span.set_attribute("app.request_id", request_id)
