import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.exceptions.base import (
    AuthenticationError,
    ModelNotFoundError,
    ProviderError,
    ProviderUnavailableError,
    RateLimitError,
)
from app.schemas.error import ErrorCode, ErrorResponse

logger = logging.getLogger(__name__)


def _get_request_id(request: Request) -> str | None:
    context = getattr(request.state, "context", None)
    return context.request_id if context else None


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AuthenticationError)
    async def handle_authentication_error(
        request: Request, exc: AuthenticationError
    ) -> JSONResponse:
        request_id = _get_request_id(request)
        logger.warning("request_id=%s authentication_error %s", request_id, exc)
        return JSONResponse(
            status_code=401,
            content=ErrorResponse(
                code=ErrorCode.AUTHENTICATION_ERROR,
                message=str(exc),
                request_id=request_id,
            ).model_dump(),
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(RateLimitError)
    async def handle_rate_limit_error(
        request: Request, exc: RateLimitError
    ) -> JSONResponse:
        request_id = _get_request_id(request)
        logger.warning("request_id=%s rate_limit_exceeded %s", request_id, exc)
        retry_after = getattr(request.state, "rate_limit_reset_after", 60)
        return JSONResponse(
            status_code=429,
            content=ErrorResponse(
                code=ErrorCode.RATE_LIMIT_ERROR,
                message=str(exc),
                request_id=request_id,
            ).model_dump(),
            headers={"Retry-After": str(retry_after)},
        )

    @app.exception_handler(ModelNotFoundError)
    async def handle_model_not_found(
        request: Request, exc: ModelNotFoundError
    ) -> JSONResponse:
        request_id = _get_request_id(request)
        logger.warning("request_id=%s model_not_found %s", request_id, exc)
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(
                code=ErrorCode.MODEL_NOT_FOUND,
                message=str(exc),
                request_id=request_id,
            ).model_dump(),
        )

    @app.exception_handler(ProviderUnavailableError)
    async def handle_provider_unavailable(
        request: Request, exc: ProviderUnavailableError
    ) -> JSONResponse:
        request_id = _get_request_id(request)
        logger.error("request_id=%s provider_unavailable %s", request_id, exc)
        return JSONResponse(
            status_code=502,
            content=ErrorResponse(
                code=ErrorCode.PROVIDER_UNAVAILABLE,
                message=str(exc),
                request_id=request_id,
            ).model_dump(),
        )

    @app.exception_handler(ProviderError)
    async def handle_provider_error(
        request: Request, exc: ProviderError
    ) -> JSONResponse:
        request_id = _get_request_id(request)
        logger.error("request_id=%s provider_error %s", request_id, exc)
        return JSONResponse(
            status_code=502,
            content=ErrorResponse(
                code=ErrorCode.PROVIDER_ERROR,
                message=str(exc),
                request_id=request_id,
            ).model_dump(),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        request_id = _get_request_id(request)
        logger.exception("request_id=%s unhandled_error %s", request_id, exc)
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                code=ErrorCode.INTERNAL_ERROR,
                message="An unexpected error occurred.",
                request_id=request_id,
            ).model_dump(),
        )
