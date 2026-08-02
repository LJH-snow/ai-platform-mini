import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)


def setup_logging(log_level: str) -> None:
    logging.basicConfig(
        level=log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            latency = time.perf_counter() - start
            logger.exception(
                "%s %s status=500 latency=%.3fs",
                request.method,
                request.url.path,
                latency,
            )
            raise
        latency = time.perf_counter() - start
        logger.info(
            "%s %s status=%d latency=%.3fs",
            request.method,
            request.url.path,
            response.status_code,
            latency,
        )
        return response
