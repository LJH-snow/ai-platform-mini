from functools import lru_cache

from app.providers.base import LLMProvider
from app.providers.factory import create_llm_provider


@lru_cache
def provide_llm_provider() -> LLMProvider:
    return create_llm_provider()
