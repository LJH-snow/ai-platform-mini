import logging
import time

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.usage.models import UsageRecord
from app.usage.service import UsageService

logger = logging.getLogger(__name__)

_usage_service: UsageService | None = None


def get_usage_service() -> UsageService:
    global _usage_service
    if _usage_service is None:
        _usage_service = UsageService()
    return _usage_service


class UsageMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        start = time.monotonic()
        response = await call_next(request)
        latency_ms = (time.monotonic() - start) * 1000

        usage_data = getattr(request.state, "usage_data", None)
        if usage_data is not None:
            api_key_name = getattr(request.state, "api_key_name", None)
            record = UsageRecord(
                request_id=getattr(request.state, "request_id", "unknown"),
                model=usage_data.get("model", "unknown"),
                prompt_tokens=usage_data.get("prompt_tokens", 0),
                completion_tokens=usage_data.get("completion_tokens", 0),
                total_tokens=usage_data.get("total_tokens", 0),
                latency_ms=latency_ms,
                api_key_name=api_key_name,
            )
            get_usage_service().record(record)

        return response
