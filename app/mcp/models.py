from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import cast

from app.tools.models import JSONSchema, JSONValue, RiskLevel


class MCPServerState(StrEnum):
    """Lifecycle state exposed for one configured MCP Server."""

    NOT_STARTED = "not_started"
    STARTING = "starting"
    READY = "ready"
    FAILED = "failed"
    CLOSED = "closed"


class MCPReadinessState(StrEnum):
    """Aggregate readiness state for the MCP integration boundary."""

    DISABLED = "disabled"
    STARTING = "starting"
    READY = "ready"
    DEGRADED = "degraded"
    NOT_READY = "not_ready"
    CLOSED = "closed"


@dataclass(frozen=True)
class MCPServerStatus:
    """Safe lifecycle and discovery summary for one MCP Server."""

    name: str
    state: MCPServerState
    tool_count: int = 0
    error_code: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe representation for health endpoints."""

        return {
            "name": self.name,
            "status": self.state.value,
            "tool_count": self.tool_count,
            "error_code": self.error_code,
        }


@dataclass(frozen=True)
class MCPReadiness:
    """Aggregate MCP readiness without performing an active server probe."""

    state: MCPReadinessState
    servers: tuple[MCPServerStatus, ...] = ()

    @property
    def is_ready(self) -> bool:
        """Whether MCP can be used without blocking application readiness."""

        return self.state in {
            MCPReadinessState.DISABLED,
            MCPReadinessState.READY,
            MCPReadinessState.DEGRADED,
        }

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe representation for health endpoints."""

        return {
            "status": self.state.value,
            "ready": self.is_ready,
            "servers": [server.to_dict() for server in self.servers],
        }


@dataclass(frozen=True)
class MCPServerConfig:
    """Configuration for one explicitly allowed MCP server process."""

    name: str
    command: tuple[str, ...]
    allowed_tools: frozenset[str] = frozenset()
    max_risk_level: RiskLevel = RiskLevel.LOW
    startup_timeout_seconds: float = 10.0
    request_timeout_seconds: float = 10.0
    environment: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("MCP server name must not be empty")
        if not self.command or not self.command[0].strip():
            raise ValueError("MCP server command must not be empty")
        if self.startup_timeout_seconds <= 0:
            raise ValueError("startup_timeout_seconds must be greater than zero")
        if self.request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be greater than zero")
        object.__setattr__(self, "command", tuple(self.command))
        object.__setattr__(self, "allowed_tools", frozenset(self.allowed_tools))
        object.__setattr__(
            self,
            "max_risk_level",
            RiskLevel(self.max_risk_level),
        )
        object.__setattr__(self, "environment", dict(self.environment))


@dataclass(frozen=True)
class MCPToolDefinition:
    """Tool metadata returned by an MCP server's ``tools/list`` method."""

    name: str
    description: str
    input_schema: JSONSchema
    output_schema: JSONSchema = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "content": {"type": "array"},
                "error": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            },
            "required": ["ok", "content", "error"],
            "additionalProperties": False,
        }
    )
    risk_level: RiskLevel = RiskLevel.LOW
    risk_metadata_known: bool = True

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("MCP tool name must not be empty")
        if not self.description.strip():
            raise ValueError("MCP tool description must not be empty")
        if not isinstance(self.input_schema, dict):
            raise TypeError("MCP tool input_schema must be a dict")
        object.__setattr__(self, "risk_level", RiskLevel(self.risk_level))
        object.__setattr__(self, "risk_metadata_known", bool(self.risk_metadata_known))


@dataclass(frozen=True)
class MCPToolCallResult:
    """Normalized result of one MCP ``tools/call`` operation."""

    content: tuple[JSONValue, ...]
    is_error: bool = False

    @classmethod
    def from_payload(cls, payload: object) -> MCPToolCallResult:
        if not isinstance(payload, dict):
            raise ValueError("MCP tools/call result must be an object")
        raw_content = payload.get("content", [])
        if not isinstance(raw_content, list):
            raise ValueError("MCP tools/call content must be a list")
        content: list[JSONValue] = []
        for item in raw_content:
            content.append(cast(JSONValue, item))
        return cls(
            content=tuple(content),
            is_error=bool(payload.get("isError", False)),
        )
