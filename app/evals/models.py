"""Typed models for deterministic offline golden evaluations."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TypeGuard

from app.agents.models import AgentRunResult

type JSONValue = (
    str | int | float | bool | None | list["JSONValue"] | dict[str, "JSONValue"]
)


def _is_json_value(value: object) -> TypeGuard[JSONValue]:
    """Return whether a value can be represented by strict JSON."""

    if value is None or isinstance(value, (str, int, bool)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _is_json_value(item) for key, item in value.items()
        )
    return False


def _normalize_optional_strings(
    value: str | Sequence[str] | None,
    *,
    field_name: str,
) -> tuple[str, ...] | None:
    """Normalize an optional string or string sequence into a tuple."""

    if value is None:
        return None
    values = (value,) if isinstance(value, str) else tuple(value)
    normalized: list[str] = []
    for item in values:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field_name} values must be non-empty strings")
        normalized.append(item)
    return tuple(normalized)


def _parse_string_sequence(
    value: object,
    *,
    field_name: str,
) -> tuple[str, ...] | None:
    """Parse a JSON string or array of strings without executing code."""

    if value is None:
        return None
    if isinstance(value, str):
        return _normalize_optional_strings(value, field_name=field_name)
    if isinstance(value, list):
        return _normalize_optional_strings(value, field_name=field_name)
    raise TypeError(f"{field_name} must be a string, array, or null")


@dataclass(frozen=True)
class EvalCase:
    """One serializable, model-agnostic golden evaluation contract."""

    case_id: str
    input: str
    expected_answer_contains: str | Sequence[str] | None = None
    expected_tools: str | Sequence[str] | None = None
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)
    max_steps: int = 8

    def __post_init__(self) -> None:
        """Validate and normalize the golden-data contract."""

        if not isinstance(self.case_id, str) or not self.case_id.strip():
            raise ValueError("case_id must not be empty")
        if not isinstance(self.input, str) or not self.input.strip():
            raise ValueError("input must not be empty")
        if not isinstance(self.max_steps, int) or isinstance(self.max_steps, bool):
            raise TypeError("max_steps must be an integer")
        if self.max_steps <= 0:
            raise ValueError("max_steps must be greater than zero")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("metadata must be a mapping")

        metadata = dict(self.metadata)
        if not all(isinstance(key, str) for key in metadata):
            raise TypeError("metadata keys must be strings")
        if not all(_is_json_value(value) for value in metadata.values()):
            raise TypeError("metadata values must be strict JSON values")

        object.__setattr__(
            self,
            "expected_answer_contains",
            _normalize_optional_strings(
                self.expected_answer_contains,
                field_name="expected_answer_contains",
            ),
        )
        object.__setattr__(
            self,
            "expected_tools",
            _normalize_optional_strings(
                self.expected_tools,
                field_name="expected_tools",
            ),
        )
        object.__setattr__(self, "metadata", metadata)

    @property
    def has_expected_tools(self) -> bool:
        """Return whether this case declares a tool-selection expectation."""

        return self.expected_tools is not None

    def to_dict(self) -> dict[str, JSONValue]:
        """Return the stable JSON object used by the JSONL contract."""

        return {
            "case_id": self.case_id,
            "input": self.input,
            "expected_answer_contains": (
                None
                if self.expected_answer_contains is None
                else list(self.expected_answer_contains)
            ),
            "expected_tools": (
                None if self.expected_tools is None else list(self.expected_tools)
            ),
            "metadata": dict(self.metadata),
            "max_steps": self.max_steps,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> EvalCase:
        """Construct a case from one validated JSON object."""

        allowed = {
            "case_id",
            "input",
            "expected_answer_contains",
            "expected_tools",
            "metadata",
            "max_steps",
        }
        unknown = set(payload) - allowed
        if unknown:
            names = ", ".join(sorted(str(name) for name in unknown))
            raise ValueError(f"unknown golden case fields: {names}")
        missing = {"case_id", "input"} - set(payload)
        if missing:
            names = ", ".join(sorted(missing))
            raise ValueError(f"missing golden case fields: {names}")

        case_id = payload["case_id"]
        input_text = payload["input"]
        if not isinstance(case_id, str):
            raise TypeError("case_id must be a string")
        if not isinstance(input_text, str):
            raise TypeError("input must be a string")

        metadata_value = payload.get("metadata", {})
        if not isinstance(metadata_value, Mapping):
            raise TypeError("metadata must be a JSON object")
        metadata: dict[str, JSONValue] = {}
        for key, value in metadata_value.items():
            if not isinstance(key, str) or not _is_json_value(value):
                raise TypeError("metadata must contain only strict JSON values")
            metadata[key] = value

        max_steps_value = payload.get("max_steps", 8)
        if not isinstance(max_steps_value, int) or isinstance(max_steps_value, bool):
            raise TypeError("max_steps must be an integer")
        return cls(
            case_id=case_id,
            input=input_text,
            expected_answer_contains=_parse_string_sequence(
                payload.get("expected_answer_contains"),
                field_name="expected_answer_contains",
            ),
            expected_tools=_parse_string_sequence(
                payload.get("expected_tools"),
                field_name="expected_tools",
            ),
            metadata=metadata,
            max_steps=max_steps_value,
        )


@dataclass(frozen=True)
class EvalExecution:
    """Normalized observations used by the runner and deterministic tests."""

    answer: str | None = None
    tool_sequence: tuple[str, ...] = ()
    steps: int = 0
    token_usage: int = 0
    status: str = "completed"
    error: str | None = None
    latency_ms: float | None = None

    def __post_init__(self) -> None:
        """Reject malformed runner observations early."""

        if any(
            not isinstance(name, str) or not name.strip() for name in self.tool_sequence
        ):
            raise ValueError("tool_sequence values must be non-empty strings")
        if not isinstance(self.steps, int) or isinstance(self.steps, bool):
            raise TypeError("steps must be an integer")
        if self.steps < 0:
            raise ValueError("steps must not be negative")
        if not isinstance(self.token_usage, int) or isinstance(self.token_usage, bool):
            raise TypeError("token_usage must be an integer")
        if self.token_usage < 0:
            raise ValueError("token_usage must not be negative")
        if not isinstance(self.status, str) or not self.status.strip():
            raise ValueError("status must not be empty")
        if self.latency_ms is not None:
            if not math.isfinite(self.latency_ms) or self.latency_ms < 0:
                raise ValueError("latency_ms must be a finite non-negative number")

    @classmethod
    def from_agent_run_result(cls, result: AgentRunResult) -> EvalExecution:
        """Extract runtime answer, tools, steps, status, and tokens."""

        tool_sequence = tuple(
            tool_call.name
            for step in result.state.steps
            for tool_call in step.decision.tool_calls
        )
        return cls(
            answer=result.answer,
            tool_sequence=tool_sequence,
            steps=len(result.state.steps),
            token_usage=result.token_usage,
            status=result.status.value,
            error=result.error,
        )


@dataclass(frozen=True)
class EvalCaseResult:
    """Evaluation outcome and runtime observations for one golden case."""

    case_id: str
    status: str
    success: bool
    answer: str | None
    answer_correct: bool
    actual_tools: tuple[str, ...]
    tool_selection_correct: bool | None
    steps: int
    latency_ms: float
    token_usage: int
    error: str | None = None

    @property
    def answer_matches(self) -> bool:
        """Backward-friendly name for the answer expectation result."""

        return self.answer_correct


@dataclass(frozen=True)
class EvaluationSummary:
    """Aggregate metrics for one ordered evaluation batch."""

    case_count: int
    successful_cases: int
    task_success_rate: float
    tool_selection_accuracy: float | None
    tool_selection_case_count: int
    average_steps: float
    p95_latency_ms: float
    total_tokens: int
    average_tokens: float

    @property
    def total_token_usage(self) -> int:
        """Backward-friendly alias for the total token metric."""

        return self.total_tokens

    @property
    def average_token_usage(self) -> float:
        """Backward-friendly alias for the average token metric."""

        return self.average_tokens


@dataclass(frozen=True)
class EvaluationReport:
    """Ordered case results together with aggregate evaluation metrics."""

    results: tuple[EvalCaseResult, ...]
    summary: EvaluationSummary

    @property
    def case_results(self) -> tuple[EvalCaseResult, ...]:
        """Return the ordered case results."""

        return self.results


# Keep the initial draft API usable while exposing the requested names.
GoldenEvalCase = EvalCase
EvalSummary = EvaluationSummary
