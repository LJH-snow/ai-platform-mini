import os
from dataclasses import dataclass
from functools import lru_cache


def _get_float_env(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    try:
        return float(raw_value)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    ollama_base_url: str
    ollama_default_model: str
    ollama_timeout_seconds: float


@lru_cache
def get_settings() -> Settings:
    return Settings(
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        ollama_default_model=os.getenv("OLLAMA_MODEL", "qwen3:4b"),
        ollama_timeout_seconds=_get_float_env("OLLAMA_TIMEOUT", 60.0),
    )
