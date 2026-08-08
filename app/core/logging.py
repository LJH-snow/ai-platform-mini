import json
import logging
import logging.config
import time
from collections.abc import MutableMapping
from datetime import UTC, datetime
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in (
            "request_id",
            "model",
            "latency_ms",
            "tokens",
            "status_code",
            "invalid_json_line_count",
            "max_invalid_json_line_length",
        ):
            if hasattr(record, key):
                log_entry[key] = getattr(record, key)
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = str(record.exc_info[1])
        return json.dumps(log_entry, ensure_ascii=False)


def setup_logging(
    log_level: str,
    log_format: str = "json",
    log_file: str | None = None,
) -> None:
    """Configure the root logger via dictConfig.

    ``log_format`` selects the console formatter (json or console text);
    ``log_file`` optionally adds a rotating file handler so production
    deployments can persist logs without a sidecar collector.
    """
    formatter_key = "json" if log_format == "json" else "console"
    handlers: dict[str, Any] = {
        "stdout": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": formatter_key,
            "level": log_level.upper(),
        },
    }
    handler_names = ["stdout"]
    if log_file:
        handlers["file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": log_file,
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 5,
            "encoding": "utf-8",
            "formatter": formatter_key,
            "level": log_level.upper(),
        }
        handler_names.append("file")
    config: dict[str, Any] = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json": {"()": "app.core.logging.JsonFormatter"},
            "console": {
                "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "handlers": handlers,
        "root": {
            "handlers": handler_names,
            "level": log_level.upper(),
        },
    }
    logging.config.dictConfig(config)


class RequestLogger(logging.LoggerAdapter):
    def process(
        self, msg: object, kwargs: MutableMapping[str, Any]
    ) -> tuple[object, MutableMapping[str, Any]]:
        extra: dict[str, Any] = kwargs.get("extra", {})  # type: ignore[assignment]
        default_id: str = "unknown"
        if self.extra:
            default_id = str(self.extra.get("request_id", "unknown"))
        extra["request_id"] = default_id
        kwargs["extra"] = extra
        return msg, kwargs


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        start = time.perf_counter()
        from app.core.context import RequestContext

        context: RequestContext | None = getattr(request.state, "context", None)
        request_id = context.request_id if context else "unknown"
        adapter = RequestLogger(logger, {"request_id": request_id})

        try:
            response = await call_next(request)
        except Exception:
            latency_ms = (time.perf_counter() - start) * 1000
            adapter.exception(
                "%s %s",
                request.method,
                request.url.path,
                extra={
                    "status_code": 500,
                    "latency_ms": round(latency_ms, 2),
                },
            )
            raise
        latency_ms = (time.perf_counter() - start) * 1000
        adapter.info(
            "%s %s",
            request.method,
            request.url.path,
            extra={
                "status_code": response.status_code,
                "latency_ms": round(latency_ms, 2),
            },
        )
        return response
