"""Agent Definition data models (Sprint B)."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class AgentRecord:
    id: str = ""
    workspace_id: str = ""
    name: str = ""
    model: str = ""
    prompt_ref: str = ""
    temperature: float = 0.7
    max_steps: int = 10
    enabled: bool = True
    created_by: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class AgentToolRecord:
    id: int = 0
    agent_id: str = ""
    tool_name: str = ""


@dataclass
class ToolRecord:
    name: str = ""
    description: str = ""
    parameters_schema: dict[str, object] = field(default_factory=dict)
    enabled_by_default: bool = False
    owner: str = "builtin"
    created_at: datetime | None = None
