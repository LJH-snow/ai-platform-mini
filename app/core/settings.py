from __future__ import annotations

import json
from collections.abc import Mapping
from functools import lru_cache
from typing import TYPE_CHECKING, Literal, cast

from pydantic import AliasChoices, Field, SecretStr, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from app.mcp.models import MCPServerConfig

RAG_EMBEDDING_DIMENSIONS = 768


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
    conversation_storage: Literal["memory", "postgres"] = "memory"
    workflow_storage: Literal["memory", "postgres"] = "memory"
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

    rag_enabled: bool = False
    rag_embedding_model: str = "nomic-embed-text"
    rag_embedding_dimensions: int = Field(default=RAG_EMBEDDING_DIMENSIONS)
    rag_chunk_size: int = Field(default=500, gt=0)
    rag_chunk_overlap: int = Field(default=50, ge=0)
    rag_top_k: int = Field(default=5, gt=0, le=50)
    rag_max_context_chars: int = Field(default=10000, gt=0, le=100000)
    rag_max_distance: float = Field(default=0.35, ge=0, le=2)
    rag_embedding_timeout_seconds: float = Field(default=60.0, gt=0)
    rag_max_upload_bytes: int = Field(default=10_000_000, gt=0, le=50_000_000)
    rag_max_pdf_pages: int = Field(default=100, gt=0, le=1_000)
    rag_max_document_characters: int = Field(default=1_000_000, gt=0, le=10_000_000)
    # Search mode: vector-only by default (byte-identical legacy behavior).
    # Switch to hybrid after the golden-set gate confirms hybrid >= vector
    # on the evaluation dataset (Sprint C milestone).
    rag_search_mode: Literal["hybrid", "vector", "keyword"] = "vector"
    rag_rrf_k: int = Field(default=60, ge=1, le=200)

    mcp_enabled: bool = False
    mcp_servers_json: str = ""

    telemetry_enabled: bool = False
    telemetry_service_name: str = "ai-platform-mini"
    telemetry_exporter: Literal["otlp", "console"] = "otlp"
    telemetry_sampling_ratio: float = 1.0
    telemetry_metrics_enabled: bool = True
    telemetry_otlp_endpoint: str = Field(
        default="http://localhost:4318/v1/traces",
        validation_alias=AliasChoices(
            "telemetry_otlp_endpoint", "otel_exporter_otlp_endpoint"
        ),
    )

    @field_validator("telemetry_sampling_ratio")
    @classmethod
    def validate_telemetry_sampling_ratio(cls, v: float) -> float:
        if v < 0.0 or v > 1.0:
            raise ValueError(
                "telemetry_sampling_ratio must be between 0.0 and 1.0 "
                f"(inclusive), got {v}"
            )
        return v

    @field_validator("rag_embedding_dimensions")
    @classmethod
    def validate_embedding_dimensions(cls, v: int) -> int:
        if v != RAG_EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"rag_embedding_dimensions must be {RAG_EMBEDDING_DIMENSIONS} "
                "(MVP fixed schema)"
            )
        return v

    @field_validator("rag_chunk_overlap")
    @classmethod
    def validate_chunk_overlap(cls, v: int, info: ValidationInfo) -> int:
        chunk_size = info.data.get("rag_chunk_size")
        if chunk_size is not None and v >= chunk_size:
            raise ValueError("rag_chunk_overlap must be less than rag_chunk_size")
        return v

    @field_validator("auth_storage")
    @classmethod
    def validate_auth_storage(cls, v: str) -> str:
        allowed = {"memory", "postgres"}
        if v not in allowed:
            raise ValueError(f"auth_storage must be one of {allowed}, got '{v}'")
        return v

    @field_validator("conversation_storage")
    @classmethod
    def validate_conversation_storage(cls, v: str) -> str:
        allowed = {"memory", "postgres"}
        if v not in allowed:
            raise ValueError(
                f"conversation_storage must be one of {allowed}, got '{v}'"
            )
        return v

    @field_validator("workflow_storage")
    @classmethod
    def validate_workflow_storage(cls, v: str) -> str:
        allowed = {"memory", "postgres"}
        if v not in allowed:
            raise ValueError(f"workflow_storage must be one of {allowed}, got '{v}'")
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

    def get_mcp_server_configs(self) -> tuple[MCPServerConfig, ...]:
        """Parse the explicitly configured MCP server allowlist."""

        if not self.mcp_enabled or not self.mcp_servers_json.strip():
            return ()

        try:
            decoded = json.loads(self.mcp_servers_json)
        except json.JSONDecodeError as exc:
            raise ValueError("mcp_servers_json must be valid JSON") from exc

        if not isinstance(decoded, list):
            raise ValueError("mcp_servers_json must be a JSON array")

        configs: list[MCPServerConfig] = []
        seen_names: set[str] = set()
        for index, raw_config in enumerate(decoded):
            if not isinstance(raw_config, Mapping):
                raise ValueError(f"MCP server at index {index} must be an object")
            config = self._parse_mcp_server_config(index, raw_config)
            if config.name in seen_names:
                raise ValueError(f"duplicate MCP server name: {config.name}")
            seen_names.add(config.name)
            configs.append(config)
        return tuple(configs)

    @staticmethod
    def _parse_mcp_server_config(
        index: int,
        raw_config: Mapping[object, object],
    ) -> MCPServerConfig:
        from app.mcp.models import MCPServerConfig
        from app.tools.models import RiskLevel

        name = Settings._required_string(raw_config, "name", index)
        command = Settings._required_string_tuple(raw_config, "command", index)
        allowed_tools = Settings._optional_string_tuple(
            raw_config, "allowed_tools", index
        )
        environment = Settings._optional_string_mapping(
            raw_config, "environment", index
        )
        raw_risk_level = raw_config.get("max_risk_level", RiskLevel.LOW.value)
        if not isinstance(raw_risk_level, str):
            raise ValueError(
                f"MCP server at index {index} max_risk_level must be a string"
            )
        try:
            risk_level = RiskLevel(raw_risk_level)
        except ValueError as exc:
            raise ValueError(
                f"MCP server at index {index} max_risk_level is invalid"
            ) from exc

        return MCPServerConfig(
            name=name,
            command=command,
            allowed_tools=frozenset(allowed_tools),
            max_risk_level=risk_level,
            startup_timeout_seconds=Settings._positive_float(
                raw_config.get("startup_timeout_seconds", 10.0),
                "startup_timeout_seconds",
                index,
            ),
            request_timeout_seconds=Settings._positive_float(
                raw_config.get("request_timeout_seconds", 10.0),
                "request_timeout_seconds",
                index,
            ),
            environment=environment,
        )

    @staticmethod
    def _required_string(
        raw_config: Mapping[object, object], key: str, index: int
    ) -> str:
        value = raw_config.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"MCP server at index {index} {key} must be a non-empty string"
            )
        return value

    @staticmethod
    def _required_string_tuple(
        raw_config: Mapping[object, object], key: str, index: int
    ) -> tuple[str, ...]:
        value = raw_config.get(key)
        if not isinstance(value, list) or not value:
            raise ValueError(
                f"MCP server at index {index} {key} must be a non-empty array"
            )
        result = Settings._string_tuple(value, key, index)
        if not result:
            raise ValueError(f"MCP server at index {index} {key} must not be empty")
        return result

    @staticmethod
    def _optional_string_tuple(
        raw_config: Mapping[object, object], key: str, index: int
    ) -> tuple[str, ...]:
        value = raw_config.get(key, [])
        if not isinstance(value, list):
            raise ValueError(f"MCP server at index {index} {key} must be an array")
        return Settings._string_tuple(value, key, index)

    @staticmethod
    def _string_tuple(value: list[object], key: str, index: int) -> tuple[str, ...]:
        if any(not isinstance(item, str) or not item.strip() for item in value):
            raise ValueError(f"MCP server at index {index} {key} must contain strings")
        return tuple(cast(str, item) for item in value)

    @staticmethod
    def _optional_string_mapping(
        raw_config: Mapping[object, object], key: str, index: int
    ) -> dict[str, str]:
        value = raw_config.get(key, {})
        if not isinstance(value, Mapping):
            raise ValueError(f"MCP server at index {index} {key} must be an object")
        if any(
            not isinstance(name, str) or not name.strip() or not isinstance(item, str)
            for name, item in value.items()
        ):
            raise ValueError(
                f"MCP server at index {index} {key} must map non-empty names to strings"
            )
        return {cast(str, name): cast(str, item) for name, item in value.items()}

    @staticmethod
    def _positive_float(value: object, key: str, index: int) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise ValueError(f"MCP server at index {index} {key} must be positive")
        return float(value)


@lru_cache
def get_settings() -> Settings:
    return Settings()
