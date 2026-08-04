from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from app.agents.models import ToolContext

__all__ = [
    "JSONSchema",
    "JSONValue",
    "RiskLevel",
    "ToolContext",
    "ToolDescriptor",
    "ToolExecutionResult",
    "ToolExecutionStatus",
]

type JSONValue = (
    str | int | float | bool | None | list["JSONValue"] | dict[str, "JSONValue"]
)
type JSONSchema = Mapping[str, object]


class RiskLevel(StrEnum):
    """Risk classification used by the tool boundary."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ToolExecutionStatus(StrEnum):
    """Normalized outcomes returned by ``ToolExecutor``."""

    SUCCEEDED = "succeeded"
    INVALID_ARGUMENTS = "invalid_arguments"
    NOT_FOUND = "not_found"
    PERMISSION_DENIED = "permission_denied"
    TIMED_OUT = "timed_out"
    FAILED = "failed"


@dataclass(frozen=True)
class ToolDescriptor:
    """Tool metadata used for registry inspection and model tool schemas."""

    name: str
    description: str
    input_schema: JSONSchema
    output_schema: JSONSchema = field(default_factory=lambda: {"type": "string"})
    risk_level: RiskLevel = RiskLevel.LOW
    required_permissions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("tool name must not be empty")
        if not self.description.strip():
            raise ValueError("tool description must not be empty")
        if not isinstance(self.input_schema, Mapping):
            raise TypeError("input_schema must be a mapping")
        if not isinstance(self.output_schema, Mapping):
            raise TypeError("output_schema must be a mapping")
        if isinstance(self.risk_level, str):
            object.__setattr__(self, "risk_level", RiskLevel(self.risk_level))
        object.__setattr__(
            self,
            "required_permissions",
            tuple(self.required_permissions),
        )

    def to_model_schema(self) -> dict[str, object]:
        """Return the stable function schema consumed by chat models."""

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": dict(self.input_schema),
            },
        }


@dataclass(frozen=True)
class ToolExecutionResult:
    """Safe, normalized result of one tool invocation."""

    tool_name: str
    status: ToolExecutionStatus
    output: str
    error_code: str | None = None
    error_message: str | None = None
    truncated: bool = False

    @property
    def succeeded(self) -> bool:
        """Whether the tool completed successfully."""

        return self.status is ToolExecutionStatus.SUCCEEDED

    @property
    def content(self) -> str:
        """Alias used when adapting the result to an agent message."""

        return self.output

    @property
    def error(self) -> str | None:
        """Backward-friendly alias for the normalized error message."""

        return self.error_message
