class AppError(Exception):
    """Base exception for all application-level errors."""


class AuthenticationError(AppError):
    """Raised when authentication fails (invalid or missing API key)."""


class ProviderError(AppError):
    """Raised when a provider backend cannot satisfy a request."""


class ProviderUnavailableError(ProviderError):
    """Raised when the provider backend is unreachable."""


class ModelNotFoundError(ProviderError):
    """Raised when the requested model does not exist on the provider."""


class ProviderRequestError(ProviderError):
    """Raised when the provider returns an unexpected error response."""
