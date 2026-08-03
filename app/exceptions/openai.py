from app.exceptions.base import (
    ModelNotFoundError,
    ProviderError,
    ProviderRequestError,
    ProviderUnavailableError,
)


class OpenAIProviderError(ProviderError):
    """Raised when the OpenAI API cannot satisfy a request."""


class OpenAIUnavailableError(OpenAIProviderError, ProviderUnavailableError):
    """Raised when the OpenAI API is unreachable."""


class OpenAIModelNotFoundError(OpenAIProviderError, ModelNotFoundError):
    """Raised when the requested OpenAI model does not exist."""


class OpenAIRequestError(OpenAIProviderError, ProviderRequestError):
    """Raised when OpenAI returns an unsuccessful HTTP response."""
