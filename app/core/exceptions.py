import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.exceptions.base import (
    APIKeyNotFoundError,
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    ConversationNotFoundError,
    KnowledgeBaseEmptyError,
    MemoryNotFoundError,
    ModelNotFoundError,
    NoRelevantContextError,
    ProviderError,
    ProviderUnavailableError,
    QuotaExceededError,
    QuotaReservationError,
    RAGDocumentTooLargeError,
    RAGDocumentValidationError,
    RAGStorageUnavailableError,
    RAGUnavailableError,
    RateLimitError,
    ValidationError,
    WorkflowNotFoundError,
)
from app.schemas.error import ErrorCode, ErrorResponse

logger = logging.getLogger(__name__)


def _get_request_id(request: Request) -> str | None:
    context = getattr(request.state, "context", None)
    return context.request_id if context else None


def _get_thread_id(request: Request) -> str | None:
    return getattr(request.state, "thread_id", None)


def _error_payload(
    request: Request, code: ErrorCode, message: str
) -> dict[str, object]:
    return ErrorResponse(
        code=code,
        message=message,
        request_id=_get_request_id(request),
        thread_id=_get_thread_id(request),
    ).model_dump()


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AuthenticationError)
    async def handle_authentication_error(
        request: Request, exc: AuthenticationError
    ) -> JSONResponse:
        request_id = _get_request_id(request)
        logger.warning("request_id=%s authentication_error %s", request_id, exc)
        return JSONResponse(
            status_code=401,
            content=_error_payload(request, ErrorCode.AUTHENTICATION_ERROR, str(exc)),
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(AuthorizationError)
    async def handle_authorization_error(
        request: Request, exc: AuthorizationError
    ) -> JSONResponse:
        request_id = _get_request_id(request)
        logger.warning("request_id=%s authorization_error %s", request_id, exc)
        return JSONResponse(
            status_code=403,
            content=_error_payload(request, ErrorCode.AUTHORIZATION_ERROR, str(exc)),
        )

    @app.exception_handler(ValidationError)
    async def handle_validation_error(
        request: Request, exc: ValidationError
    ) -> JSONResponse:
        request_id = _get_request_id(request)
        logger.warning("request_id=%s validation_error %s", request_id, exc)
        return JSONResponse(
            status_code=422,
            content=_error_payload(request, ErrorCode.VALIDATION_ERROR, str(exc)),
        )

    @app.exception_handler(ConflictError)
    async def handle_conflict_error(
        request: Request, exc: ConflictError
    ) -> JSONResponse:
        request_id = _get_request_id(request)
        logger.warning("request_id=%s conflict_error %s", request_id, exc)
        return JSONResponse(
            status_code=409,
            content=_error_payload(request, ErrorCode.CONFLICT_ERROR, str(exc)),
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
            content=_error_payload(request, ErrorCode.RATE_LIMIT_ERROR, str(exc)),
            headers={"Retry-After": str(retry_after)},
        )

    @app.exception_handler(QuotaExceededError)
    async def handle_quota_exceeded(
        request: Request, exc: QuotaExceededError
    ) -> JSONResponse:
        request_id = _get_request_id(request)
        logger.warning("request_id=%s quota_exceeded %s", request_id, exc)
        return JSONResponse(
            status_code=429,
            content=_error_payload(request, ErrorCode.QUOTA_EXCEEDED, str(exc)),
            headers={"Retry-After": str(exc.retry_after)},
        )

    @app.exception_handler(QuotaReservationError)
    async def handle_quota_reservation_error(
        request: Request, exc: QuotaReservationError
    ) -> JSONResponse:
        request_id = _get_request_id(request)
        logger.error("request_id=%s quota_unavailable %s", request_id, exc)
        return JSONResponse(
            status_code=503,
            content=_error_payload(request, ErrorCode.QUOTA_UNAVAILABLE, str(exc)),
        )

    @app.exception_handler(ModelNotFoundError)
    async def handle_model_not_found(
        request: Request, exc: ModelNotFoundError
    ) -> JSONResponse:
        request_id = _get_request_id(request)
        logger.warning("request_id=%s model_not_found %s", request_id, exc)
        return JSONResponse(
            status_code=404,
            content=_error_payload(request, ErrorCode.MODEL_NOT_FOUND, str(exc)),
        )

    @app.exception_handler(APIKeyNotFoundError)
    async def handle_api_key_not_found(
        request: Request, exc: APIKeyNotFoundError
    ) -> JSONResponse:
        request_id = _get_request_id(request)
        logger.warning("request_id=%s api_key_not_found %s", request_id, exc)
        return JSONResponse(
            status_code=404,
            content=_error_payload(request, ErrorCode.API_KEY_NOT_FOUND, str(exc)),
        )

    @app.exception_handler(ConversationNotFoundError)
    async def handle_conversation_not_found(
        request: Request, exc: ConversationNotFoundError
    ) -> JSONResponse:
        request_id = _get_request_id(request)
        logger.warning("request_id=%s conversation_not_found %s", request_id, exc)
        return JSONResponse(
            status_code=404,
            content=_error_payload(request, ErrorCode.CONVERSATION_NOT_FOUND, str(exc)),
        )

    @app.exception_handler(MemoryNotFoundError)
    async def handle_memory_not_found(
        request: Request, exc: MemoryNotFoundError
    ) -> JSONResponse:
        request_id = _get_request_id(request)
        logger.warning("request_id=%s memory_not_found %s", request_id, exc)
        return JSONResponse(
            status_code=404,
            content=_error_payload(request, ErrorCode.MEMORY_NOT_FOUND, str(exc)),
        )

    @app.exception_handler(WorkflowNotFoundError)
    async def handle_workflow_not_found(
        request: Request, exc: WorkflowNotFoundError
    ) -> JSONResponse:
        request_id = _get_request_id(request)
        logger.warning("request_id=%s workflow_not_found %s", request_id, exc)
        return JSONResponse(
            status_code=404,
            content=_error_payload(request, ErrorCode.WORKFLOW_NOT_FOUND, str(exc)),
        )

    @app.exception_handler(ProviderUnavailableError)
    async def handle_provider_unavailable(
        request: Request, exc: ProviderUnavailableError
    ) -> JSONResponse:
        request_id = _get_request_id(request)
        logger.error("request_id=%s provider_unavailable %s", request_id, exc)
        return JSONResponse(
            status_code=502,
            content=_error_payload(request, ErrorCode.PROVIDER_UNAVAILABLE, str(exc)),
        )

    @app.exception_handler(ProviderError)
    async def handle_provider_error(
        request: Request, exc: ProviderError
    ) -> JSONResponse:
        request_id = _get_request_id(request)
        logger.error("request_id=%s provider_error %s", request_id, exc)
        return JSONResponse(
            status_code=502,
            content=_error_payload(request, ErrorCode.PROVIDER_ERROR, str(exc)),
        )

    @app.exception_handler(RAGUnavailableError)
    async def handle_rag_unavailable(
        request: Request, exc: RAGUnavailableError
    ) -> JSONResponse:
        request_id = _get_request_id(request)
        logger.error("request_id=%s rag_unavailable %s", request_id, exc)
        return JSONResponse(
            status_code=503,
            content=_error_payload(request, ErrorCode.RAG_UNAVAILABLE, str(exc)),
        )

    @app.exception_handler(KnowledgeBaseEmptyError)
    async def handle_knowledge_base_empty(
        request: Request, exc: KnowledgeBaseEmptyError
    ) -> JSONResponse:
        request_id = _get_request_id(request)
        logger.warning("request_id=%s knowledge_base_empty %s", request_id, exc)
        return JSONResponse(
            status_code=404,
            content=_error_payload(request, ErrorCode.KNOWLEDGE_BASE_EMPTY, str(exc)),
        )

    @app.exception_handler(NoRelevantContextError)
    async def handle_no_relevant_context(
        request: Request, exc: NoRelevantContextError
    ) -> JSONResponse:
        request_id = _get_request_id(request)
        logger.warning("request_id=%s no_relevant_context %s", request_id, exc)
        return JSONResponse(
            status_code=404,
            content=_error_payload(request, ErrorCode.NO_RELEVANT_CONTEXT, str(exc)),
        )

    @app.exception_handler(RAGDocumentValidationError)
    async def handle_rag_document_validation_error(
        request: Request, exc: RAGDocumentValidationError
    ) -> JSONResponse:
        request_id = _get_request_id(request)
        logger.warning("request_id=%s rag_document_invalid %s", request_id, exc)
        return JSONResponse(
            status_code=400,
            content=_error_payload(request, ErrorCode.RAG_DOCUMENT_INVALID, str(exc)),
        )

    @app.exception_handler(RAGDocumentTooLargeError)
    async def handle_rag_document_too_large_error(
        request: Request, exc: RAGDocumentTooLargeError
    ) -> JSONResponse:
        request_id = _get_request_id(request)
        logger.warning("request_id=%s rag_document_too_large %s", request_id, exc)
        return JSONResponse(
            status_code=413,
            content=_error_payload(request, ErrorCode.RAG_DOCUMENT_TOO_LARGE, str(exc)),
        )

    @app.exception_handler(RAGStorageUnavailableError)
    async def handle_rag_storage_unavailable(
        request: Request, exc: RAGStorageUnavailableError
    ) -> JSONResponse:
        request_id = _get_request_id(request)
        logger.error("request_id=%s rag_storage_unavailable %s", request_id, exc)
        return JSONResponse(
            status_code=503,
            content=_error_payload(
                request,
                ErrorCode.RAG_STORAGE_UNAVAILABLE,
                "RAG storage is temporarily unavailable",
            ),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        request_id = _get_request_id(request)
        logger.exception("request_id=%s unhandled_error %s", request_id, exc)
        return JSONResponse(
            status_code=500,
            content=_error_payload(
                request,
                ErrorCode.INTERNAL_ERROR,
                "An unexpected error occurred.",
            ),
        )
