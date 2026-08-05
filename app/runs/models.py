from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import cast

from app.agents.models import RunStatus, StopReason


class RunTraceRecordType(StrEnum):
    """Record types used by the JSONL trace format."""

    RUN = "run"
    EVENT = "event"


@dataclass(frozen=True)
class RunTraceEvent:
    """Privacy-safe summary of one Agent Runtime event."""

    run_id: str
    sequence: int
    kind: str
    occurred_at: datetime
    elapsed_ms: float
    step_index: int | None = None
    message_summary: str | None = None
    error_summary: str | None = None
    decision_type: str | None = None
    tool_name: str | None = None
    tool_call_count: int | None = None
    tool_arguments_count: int | None = None
    tool_arguments_fingerprint: str | None = None
    tool_succeeded: bool | None = None
    tool_error_code: str | None = None
    tool_output_chars: int | None = None
    tool_output_fingerprint: str | None = None
    tool_output_truncated: bool | None = None
    status: str | None = None
    stop_reason: str | None = None
    token_usage: int | None = None
    cumulative_token_usage: int | None = None

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe trace event representation."""

        return {
            "type": RunTraceRecordType.EVENT.value,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "kind": self.kind,
            "occurred_at": self.occurred_at.isoformat(),
            "elapsed_ms": self.elapsed_ms,
            "step_index": self.step_index,
            "message_summary": self.message_summary,
            "error_summary": self.error_summary,
            "decision_type": self.decision_type,
            "tool_name": self.tool_name,
            "tool_call_count": self.tool_call_count,
            "tool_arguments_count": self.tool_arguments_count,
            "tool_arguments_fingerprint": self.tool_arguments_fingerprint,
            "tool_succeeded": self.tool_succeeded,
            "tool_error_code": self.tool_error_code,
            "tool_output_chars": self.tool_output_chars,
            "tool_output_fingerprint": self.tool_output_fingerprint,
            "tool_output_truncated": self.tool_output_truncated,
            "status": self.status,
            "stop_reason": self.stop_reason,
            "token_usage": self.token_usage,
            "cumulative_token_usage": self.cumulative_token_usage,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> RunTraceEvent:
        """Rebuild a trace event from one JSON object."""

        return cls(
            run_id=_required_str(data, "run_id"),
            sequence=_required_int(data, "sequence"),
            kind=_required_str(data, "kind"),
            occurred_at=_parse_datetime(data, "occurred_at"),
            elapsed_ms=_required_float(data, "elapsed_ms"),
            step_index=_optional_int(data.get("step_index")),
            message_summary=_optional_str(data.get("message_summary")),
            error_summary=_optional_str(data.get("error_summary")),
            decision_type=_optional_str(data.get("decision_type")),
            tool_name=_optional_str(data.get("tool_name")),
            tool_call_count=_optional_int(data.get("tool_call_count")),
            tool_arguments_count=_optional_int(data.get("tool_arguments_count")),
            tool_arguments_fingerprint=_optional_str(
                data.get("tool_arguments_fingerprint")
            ),
            tool_succeeded=_optional_bool(data.get("tool_succeeded")),
            tool_error_code=_optional_str(data.get("tool_error_code")),
            tool_output_chars=_optional_int(data.get("tool_output_chars")),
            tool_output_fingerprint=_optional_str(data.get("tool_output_fingerprint")),
            tool_output_truncated=_optional_bool(data.get("tool_output_truncated")),
            status=_optional_str(data.get("status")),
            stop_reason=_optional_str(data.get("stop_reason")),
            token_usage=_optional_int(data.get("token_usage")),
            cumulative_token_usage=_optional_int(data.get("cumulative_token_usage")),
        )


@dataclass(frozen=True)
class RunTraceToolSummary:
    """Aggregated, payload-free summary for one tool name."""

    name: str
    call_count: int
    success_count: int
    failure_count: int
    last_error_code: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe tool summary."""

        return {
            "name": self.name,
            "call_count": self.call_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "last_error_code": self.last_error_code,
        }


@dataclass(frozen=True)
class RunTrace:
    """Privacy-safe trace for one complete or partially observed Agent Run."""

    run_id: str
    request_id: str | None
    model: str | None
    started_at: datetime
    completed_at: datetime | None
    status: RunStatus | None
    stop_reason: StopReason | None
    token_usage: int | None
    events: tuple[RunTraceEvent, ...]
    recorder_errors: tuple[str, ...] = ()

    @property
    def step_count(self) -> int:
        """Return the greatest observed runtime step index."""

        return max((event.step_index or 0 for event in self.events), default=0)

    @property
    def duration_ms(self) -> float | None:
        """Return elapsed wall-clock time when a terminal event was observed."""

        if self.completed_at is None:
            return None
        return max(0.0, (self.completed_at - self.started_at).total_seconds() * 1000)

    @property
    def tool_summaries(self) -> tuple[RunTraceToolSummary, ...]:
        """Return aggregated tool usage without arguments or outputs."""

        summaries: dict[str, list[object]] = {}
        for event in self.events:
            if event.tool_name is None or event.tool_succeeded is None:
                continue
            values = summaries.setdefault(event.tool_name, [0, 0, 0, None])
            values[0] = cast(int, values[0]) + 1
            if event.tool_succeeded:
                values[1] = cast(int, values[1]) + 1
            else:
                values[2] = cast(int, values[2]) + 1
                if event.tool_error_code is not None:
                    values[3] = event.tool_error_code
        return tuple(
            RunTraceToolSummary(
                name=name,
                call_count=cast(int, values[0]),
                success_count=cast(int, values[1]),
                failure_count=cast(int, values[2]),
                last_error_code=cast(str | None, values[3]),
            )
            for name, values in summaries.items()
        )

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe representation of the trace."""

        return {
            "type": RunTraceRecordType.RUN.value,
            "run_id": self.run_id,
            "request_id": self.request_id,
            "model": self.model,
            "started_at": self.started_at.isoformat(),
            "completed_at": (
                None if self.completed_at is None else self.completed_at.isoformat()
            ),
            "status": None if self.status is None else self.status.value,
            "stop_reason": (
                None if self.stop_reason is None else self.stop_reason.value
            ),
            "step_count": self.step_count,
            "duration_ms": self.duration_ms,
            "token_usage": self.token_usage,
            "tool_summaries": [summary.to_dict() for summary in self.tool_summaries],
            "recorder_errors": list(self.recorder_errors),
            "events": [event.to_dict() for event in self.events],
        }

    def to_jsonl(self) -> str:
        """Serialize the trace as deterministic JSONL."""

        run_record = self.to_dict()
        events = cast(list[dict[str, object]], run_record.pop("events"))
        lines = [json.dumps(run_record, sort_keys=True, separators=(",", ":"))]
        lines.extend(
            json.dumps(event, sort_keys=True, separators=(",", ":")) for event in events
        )
        return "\n".join(lines) + "\n"

    @classmethod
    def from_jsonl(cls, payload: str) -> RunTrace:
        """Deserialize a trace produced by ``to_jsonl``."""

        records: list[object] = []
        for line in payload.splitlines():
            if line.strip():
                records.append(json.loads(line))
        if not records:
            raise ValueError("trace JSONL must contain at least one record")
        run_record = records[0]
        if not isinstance(run_record, dict) or run_record.get("type") != "run":
            raise ValueError("trace JSONL must start with a run record")
        completed_at = run_record.get("completed_at")
        recorder_errors = run_record.get("recorder_errors", [])
        if not isinstance(recorder_errors, list) or not all(
            isinstance(error, str) for error in recorder_errors
        ):
            raise ValueError("trace recorder_errors must be a string list")
        event_records = records[1:]
        events = tuple(
            RunTraceEvent.from_dict(cast(Mapping[str, object], record))
            for record in event_records
            if isinstance(record, dict) and record.get("type") == "event"
        )
        return cls(
            run_id=_required_str(run_record, "run_id"),
            request_id=_optional_str(run_record.get("request_id")),
            model=_optional_str(run_record.get("model")),
            started_at=_parse_datetime(run_record, "started_at"),
            completed_at=(
                None
                if completed_at is None
                else _parse_datetime_value(completed_at, "completed_at")
            ),
            status=_optional_enum(run_record.get("status"), RunStatus),
            stop_reason=_optional_enum(run_record.get("stop_reason"), StopReason),
            token_usage=_optional_int(run_record.get("token_usage")),
            events=events,
            recorder_errors=tuple(recorder_errors),
        )


def _required_str(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"trace field {key} must be a non-empty string")
    return value


def _required_int(data: Mapping[str, object], key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"trace field {key} must be an integer")
    return value


def _required_float(data: Mapping[str, object], key: str) -> float:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"trace field {key} must be a number")
    return float(value)


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _parse_datetime(data: Mapping[str, object], key: str) -> datetime:
    return _parse_datetime_value(data.get(key), key)


def _parse_datetime_value(value: object, key: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"trace field {key} must be an ISO datetime string")
    return datetime.fromisoformat(value)


def _optional_enum[T: StrEnum](value: object, enum_type: type[T]) -> T | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("trace enum field must be a string")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ValueError(f"unknown trace enum value: {value}") from exc
