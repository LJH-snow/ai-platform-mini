class OllamaServiceError(Exception):
    """Raised when the Ollama API cannot satisfy a request."""


class OllamaModelNotFoundError(OllamaServiceError):
    """Raised when the requested Ollama model does not exist locally."""
