from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    app_name: str = "AI Platform Mini"
    debug: bool = False
    log_level: str = "INFO"

    ollama_base_url: str = "http://localhost:11434"
    ollama_default_model: str = "qwen3:4b"
    ollama_timeout_seconds: float = 60.0

    llm_provider: str = "ollama"

    api_keys: str = ""
    auth_enabled: bool = True

    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 60


@lru_cache
def get_settings() -> Settings:
    return Settings()
