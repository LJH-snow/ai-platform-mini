import pytest

from app.core.container import provide_llm_provider
from app.core.settings import get_settings
from app.providers.mock import MockProvider
from app.providers.ollama import OllamaProvider


def _clear_caches() -> None:
    provide_llm_provider.cache_clear()
    get_settings.cache_clear()


def test_factory_returns_mock_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    _clear_caches()

    provider = provide_llm_provider()
    assert isinstance(provider, MockProvider)

    _clear_caches()


def test_factory_returns_ollama_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    _clear_caches()

    provider = provide_llm_provider()
    assert isinstance(provider, OllamaProvider)

    _clear_caches()


def test_factory_raises_on_unknown_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "nonexistent")
    _clear_caches()

    with pytest.raises(ValueError, match="Unsupported LLM provider"):
        from app.providers.factory import create_llm_provider

        create_llm_provider()

    _clear_caches()


def test_provider_is_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    _clear_caches()

    provider1 = provide_llm_provider()
    provider2 = provide_llm_provider()
    assert provider1 is provider2

    _clear_caches()
