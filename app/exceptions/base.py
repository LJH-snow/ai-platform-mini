class AppError(Exception):
    """Base exception for all application-level errors."""


class AuthenticationError(AppError):
    """Raised when authentication fails (invalid or missing API key)."""


class AuthorizationError(AppError):
    """Raised when an authenticated user lacks required permissions."""


class ConflictError(AppError):
    """Raised when a request conflicts with current state (e.g. duplicate)."""


class ValidationError(AppError):
    """Raised when request parameters fail validation."""


class APIKeyNotFoundError(AppError):
    """Raised when an API key cannot be found by the given identifier."""


class ConversationNotFoundError(AppError):
    """Raised when a conversation thread is missing or not owned by the caller."""


class WorkflowNotFoundError(AppError):
    """Raised when a workflow thread is missing or not owned by the caller."""


class RateLimitError(AppError):
    """Raised when the request rate exceeds the configured limit."""


class ProviderError(AppError):
    """Raised when a provider backend cannot satisfy a request."""


class ProviderUnavailableError(ProviderError):
    """Raised when the provider backend is unreachable."""


class ModelNotFoundError(ProviderError):
    """Raised when the requested model does not exist on the provider."""


class QuotaExceededError(AppError):
    """Raised when a token quota has been exceeded."""

    def __init__(self, message: str, retry_after: int = 86400) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class QuotaReservationError(AppError):
    """Raised when an active quota reservation cannot be maintained."""


class ProviderRequestError(ProviderError):
    """Raised when the provider returns an unexpected error response."""


class RAGError(AppError):
    """Raised when the RAG subsystem cannot satisfy a request."""


class RAGUnavailableError(RAGError):
    """Raised when RAG is not enabled or the embedding service is unreachable."""


class KnowledgeBaseEmptyError(RAGError):
    """Raised when the knowledge base has no indexed documents."""


class NoRelevantContextError(RAGError):
    """Raised when no retrieved chunk passes the relevance threshold."""


class RAGStorageUnavailableError(RAGError):
    """Raised when the RAG storage backend (e.g. pgvector) is unreachable."""


class RAGDocumentValidationError(RAGError):
    """Raised when an uploaded document cannot be safely parsed."""


class RAGDocumentTooLargeError(RAGError):
    """Raised when an uploaded document exceeds the configured byte limit."""
