from app.providers.base import LLMProvider


def get_llm_provider() -> LLMProvider:
    from app.core.settings import get_settings
    from app.providers.mock import get_mock_provider
    from app.providers.ollama import get_ollama_provider

    settings = get_settings()
    if settings.llm_provider == "mock":
        return get_mock_provider()
    return get_ollama_provider()
