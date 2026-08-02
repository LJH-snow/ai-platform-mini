import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.schemas.error import ErrorCode, ErrorResponse
from app.services.ollama_service import OllamaModelNotFoundError, OllamaServiceError

logger = logging.getLogger(__name__)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(OllamaModelNotFoundError)
    async def handle_model_not_found(
        request: Request, exc: OllamaModelNotFoundError
    ) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        logger.warning("request_id=%s model_not_found %s", request_id, exc)
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(
                code=ErrorCode.MODEL_NOT_FOUND,
                message=str(exc),
                request_id=request_id,
            ).model_dump(),
        )

    @app.exception_handler(OllamaServiceError)
    async def handle_ollama_error(
        request: Request, exc: OllamaServiceError
    ) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        logger.error("request_id=%s ollama_error %s", request_id, exc)
        return JSONResponse(
            status_code=502,
            content=ErrorResponse(
                code=ErrorCode.OLLAMA_ERROR,
                message=str(exc),
                request_id=request_id,
            ).model_dump(),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        logger.exception("request_id=%s unhandled_error %s", request_id, exc)
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                code=ErrorCode.INTERNAL_ERROR,
                message="An unexpected error occurred.",
                request_id=request_id,
            ).model_dump(),
        )
