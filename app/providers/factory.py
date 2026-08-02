from app.core.settings import get_settings
from app.providers.base import LLMProvider
from app.providers.mock import get_mock_provider
from app.providers.ollama import get_ollama_provider

_SUPPORTED_PROVIDERS = {"ollama", "mock"}


def create_llm_provider() -> LLMProvider:
    settings = get_settings()
    provider_name = settings.llm_provider

    if provider_name == "mock":
        return get_mock_provider()
    if provider_name == "ollama":
        return get_ollama_provider()

    raise ValueError(
        f"Unsupported LLM provider: {provider_name!r}. "
        f"Supported: {', '.join(sorted(_SUPPORTED_PROVIDERS))}"
    )
