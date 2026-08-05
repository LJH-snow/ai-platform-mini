from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

from app.agents.models import AgentEvent, AgentEventKind
from app.runs.models import RunTraceEvent

_DEFAULT_SUMMARY_MAX_CHARS = 160
_TRUNCATION_MARKER = "...[truncated]"
_SECRET_PATTERN = re.compile(
    r"(?i)(api[_ -]?key|authorization|bearer|password|secret|token)"
    r"(\s*[:=]\s*)(['\"]?)[^\s,'\"}]+\3"
)


class DefaultRunTraceSanitizer:
    """Sanitize runtime events without retaining private payloads."""

    def __init__(self, *, summary_max_chars: int = _DEFAULT_SUMMARY_MAX_CHARS) -> None:
        if summary_max_chars < 1:
            raise ValueError("summary_max_chars must be greater than zero")
        self._summary_max_chars = summary_max_chars

    def sanitize(self, event: AgentEvent, started_at: datetime) -> RunTraceEvent:
        """Build a bounded event summary from one runtime event."""

        decision_type: str | None = None
        tool_call_count: int | None = None
        token_usage = event.cumulative_token_usage
        message_summary: str | None = None
        error_summary: str | None = None
        if event.kind is AgentEventKind.RUN_STARTED:
            message_summary = _summarize_text(
                event.message,
                max_chars=self._summary_max_chars,
                include_preview=False,
            )
        elif event.kind is AgentEventKind.ANSWER:
            message_summary = _summarize_text(
                event.message,
                max_chars=self._summary_max_chars,
            )
        elif event.message is not None:
            error_summary = _summarize_text(
                event.message,
                max_chars=self._summary_max_chars,
            )

        if event.decision is not None:
            decision_type = (
                "answer" if event.decision.answer is not None else "tool_call"
            )
            tool_call_count = len(event.decision.tool_calls)
            token_usage = event.decision.token_usage

        tool_call = event.tool_call
        tool_result = event.tool_result
        tool_name = None if tool_call is None else _safe_text(tool_call.name)
        if tool_name is None and tool_result is not None:
            tool_name = _safe_text(tool_result.name)

        tool_arguments_count = None
        tool_arguments_fingerprint = None
        if tool_call is not None:
            tool_arguments_count = len(tool_call.arguments)
            tool_arguments_fingerprint = _fingerprint(tool_call.arguments)

        tool_succeeded = None
        tool_error_code = None
        tool_output_chars = None
        tool_output_fingerprint = None
        tool_output_truncated = None
        if tool_result is not None:
            tool_succeeded = tool_result.succeeded
            tool_error_code = _safe_text(tool_result.error)
            tool_output_chars = len(tool_result.content)
            tool_output_fingerprint = _fingerprint(tool_result.content)
            tool_output_truncated = tool_result.truncated

        return RunTraceEvent(
            run_id=event.run_id,
            sequence=event.sequence,
            kind=event.kind.value,
            occurred_at=event.occurred_at,
            elapsed_ms=max(
                0.0,
                (event.occurred_at - started_at).total_seconds() * 1000,
            ),
            step_index=event.step_index,
            message_summary=message_summary,
            error_summary=error_summary,
            decision_type=decision_type,
            tool_name=tool_name,
            tool_call_count=tool_call_count,
            tool_arguments_count=tool_arguments_count,
            tool_arguments_fingerprint=tool_arguments_fingerprint,
            tool_succeeded=tool_succeeded,
            tool_error_code=tool_error_code,
            tool_output_chars=tool_output_chars,
            tool_output_fingerprint=tool_output_fingerprint,
            tool_output_truncated=tool_output_truncated,
            status=None if event.status is None else event.status.value,
            stop_reason=(
                None if event.stop_reason is None else event.stop_reason.value
            ),
            token_usage=token_usage,
            cumulative_token_usage=event.cumulative_token_usage,
        )


def _summarize_text(
    value: str | None,
    *,
    max_chars: int,
    include_preview: bool = True,
) -> str | None:
    if value is None:
        return None
    redacted = _redact(value)
    preview = redacted[:max_chars]
    if len(redacted) > max_chars:
        preview = preview[: max(0, max_chars - len(_TRUNCATION_MARKER))]
        preview += _TRUNCATION_MARKER
    preview_part = f";preview={preview}" if include_preview else ""
    return f"chars={len(value)};sha256={_sha256(value)}{preview_part}"


def _redact(value: str) -> str:
    return _SECRET_PATTERN.sub(r"\1\2<redacted>", value)


def _safe_text(value: str | None) -> str | None:
    if value is None:
        return None
    return _redact(value)[:_DEFAULT_SUMMARY_MAX_CHARS]


def _fingerprint(value: object) -> str:
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=lambda item: f"<{type(item).__name__}>",
    )
    return f"sha256:{_sha256(canonical)}"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
