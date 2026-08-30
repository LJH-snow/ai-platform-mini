"""Data models for multi-agent orchestration.

A multi-agent run consists of a Supervisor that decomposes a user task into
subtasks, and specialized Agents that execute those subtasks. The Orchestrator
manages execution order, shared context, failure policies, and budgets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class AgentRole(StrEnum):
    """Roles that specialized agents can play."""

    RESEARCH = "research"
    WRITER = "writer"
    REVIEWER = "reviewer"
    CUSTOM = "custom"


class TaskStatus(StrEnum):
    """Status of an individual subtask."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class OrchestrationStatus(StrEnum):
    """Terminal states for a multi-agent run."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    BUDGET_EXCEEDED = "budget_exceeded"


class FailurePolicy(StrEnum):
    """How to handle a failed subtask."""

    FAIL_FAST = "fail_fast"  # Stop entire run on first failure
    SKIP = "skip"  # Skip failed task, continue with others
    RETRY_ONCE = "retry_once"  # Retry once, then fail_fast or skip


@dataclass(frozen=True)
class AgentConfig:
    """Configuration for a specialized agent."""

    role: AgentRole
    name: str
    system_prompt: str
    model: str | None = None
    max_steps: int = 5
    timeout: float | None = None
    token_budget: int | None = None


@dataclass(frozen=True)
class Subtask:
    """A task decomposed by the Supervisor."""

    id: str
    description: str
    agent_role: AgentRole
    depends_on: tuple[str, ...] = ()  # IDs of tasks this depends on
    input_template: str = ""  # Template with {prev_results} placeholders
    priority: int = 0  # Higher = runs earlier when parallel


@dataclass
class SubtaskResult:
    """Result of executing one subtask."""

    task_id: str
    status: TaskStatus = TaskStatus.PENDING
    output: str = ""
    error: str | None = None
    agent_role: AgentRole = AgentRole.CUSTOM
    token_usage: int = 0
    steps_taken: int = 0
    duration_ms: int | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass
class SupervisorDecision:
    """Output from the Supervisor's task decomposition."""

    subtasks: list[Subtask]
    reasoning: str = ""
    total_estimated_tokens: int | None = None


@dataclass
class SharedContext:
    """Context shared between agents during orchestration."""

    user_input: str = ""
    task_results: dict[str, SubtaskResult] = field(default_factory=dict)
    metadata: dict[str, object] = field(default_factory=dict)

    def get_prev_results(self, task_ids: tuple[str, ...]) -> str:
        """Format previous results for injection into a task prompt."""
        if not task_ids:
            return ""
        parts: list[str] = []
        for tid in task_ids:
            result = self.task_results.get(tid)
            if result and result.status == TaskStatus.COMPLETED:
                parts.append(f"[{tid}]: {result.output}")
            elif result and result.status == TaskStatus.FAILED:
                parts.append(f"[{tid}]: FAILED - {result.error}")
        return "\n\n".join(parts)


@dataclass
class OrchestrationConfig:
    """Configuration for a multi-agent orchestration run."""

    max_concurrency: int = 3
    failure_policy: FailurePolicy = FailurePolicy.FAIL_FAST
    max_retries: int = 1
    total_timeout: float | None = 300.0  # 5 minutes default
    total_token_budget: int | None = None
    supervisor_model: str | None = None
    supervisor_max_steps: int = 3
    agent_configs: dict[AgentRole, AgentConfig] = field(default_factory=dict)


@dataclass
class OrchestrationState:
    """Mutable state tracked during a multi-agent run."""

    run_id: str = ""
    status: OrchestrationStatus = OrchestrationStatus.RUNNING
    shared_context: SharedContext = field(default_factory=SharedContext)
    subtasks: list[Subtask] = field(default_factory=list)
    results: dict[str, SubtaskResult] = field(default_factory=dict)
    total_token_usage: int = 0
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def is_terminal(self) -> bool:
        return self.status in (
            OrchestrationStatus.COMPLETED,
            OrchestrationStatus.FAILED,
            OrchestrationStatus.CANCELLED,
            OrchestrationStatus.TIMED_OUT,
            OrchestrationStatus.BUDGET_EXCEEDED,
        )


@dataclass(frozen=True)
class OrchestrationResult:
    """Final result of a multi-agent run."""

    run_id: str
    status: OrchestrationStatus
    final_output: str = ""
    subtask_results: list[SubtaskResult] = field(default_factory=list)
    total_token_usage: int = 0
    error: str | None = None
    duration_ms: int | None = None
