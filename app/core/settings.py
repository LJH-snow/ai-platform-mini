from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, ValidationInfo, field_validator
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
    log_format: str = "json"

    ollama_base_url: str = "http://localhost:11434"
    ollama_default_model: str = "qwen3:4b"
    ollama_timeout_seconds: float = 60.0

    openai_api_key: SecretStr = SecretStr("")
    openai_base_url: str = "https://api.openai.com/v1"
    openai_default_model: str = "gpt-4.1-mini"
    openai_timeout_seconds: float = 60.0

    llm_provider: str = "ollama"

    api_keys: SecretStr = SecretStr("")
    admin_api_keys: SecretStr = SecretStr("")
    auth_enabled: bool = True
    auth_storage: Literal["memory", "postgres"] = "memory"
    initial_api_key: SecretStr = SecretStr("")

    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 60

    quota_daily_tokens: int = Field(default=0, ge=0)
    quota_monthly_tokens: int = Field(default=0, ge=0)
    quota_reservation_ttl_seconds: int = Field(default=600, gt=0)
    quota_reservation_renewal_seconds: int = Field(default=60, gt=0)

    database_url: SecretStr = SecretStr(
        "postgresql+asyncpg://postgres:postgres@localhost:5432/aiplatform"
    )

    @field_validator("auth_storage")
    @classmethod
    def validate_auth_storage(cls, v: str) -> str:
        allowed = {"memory", "postgres"}
        if v not in allowed:
            raise ValueError(f"auth_storage must be one of {allowed}, got '{v}'")
        return v

    @field_validator("quota_reservation_renewal_seconds")
    @classmethod
    def validate_reservation_renewal_seconds(
        cls, value: int, info: ValidationInfo
    ) -> int:
        ttl_seconds = info.data.get("quota_reservation_ttl_seconds")
        if ttl_seconds is not None and value >= ttl_seconds:
            raise ValueError(
                "quota_reservation_renewal_seconds must be less than "
                "quota_reservation_ttl_seconds"
            )
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
