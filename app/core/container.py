from functools import lru_cache

from app.providers.base import LLMProvider
from app.providers.factory import create_llm_provider
from app.usage.service import UsageService


@lru_cache
def provide_llm_provider() -> LLMProvider:
    return create_llm_provider()


@lru_cache
def provide_usage_service() -> UsageService:
    return UsageService()
