from app.exceptions.base import (
    ModelNotFoundError,
    ProviderError,
    ProviderRequestError,
    ProviderUnavailableError,
)


class OllamaServiceError(ProviderError):
    """Raised when the Ollama API cannot satisfy a request."""


class OllamaUnavailableError(OllamaServiceError, ProviderUnavailableError):
    """Raised when the Ollama service is unreachable."""


class OllamaModelNotFoundError(OllamaServiceError, ModelNotFoundError):
    """Raised when the requested Ollama model does not exist locally."""


class OllamaRequestError(OllamaServiceError, ProviderRequestError):
    """Raised when Ollama returns an unexpected HTTP error."""
