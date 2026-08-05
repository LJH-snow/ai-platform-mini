"""Deterministic, dependency-injected evaluation runner."""

from __future__ import annotations

import math
import time
from collections.abc import Awaitable, Callable, Iterable

from app.agents.models import AgentRunResult
from app.evals.jsonl import validate_golden_dataset
from app.evals.models import (
    EvalCase,
    EvalCaseResult,
    EvalExecution,
    EvaluationReport,
    EvaluationSummary,
)

type RunCase = Callable[[EvalCase], Awaitable[AgentRunResult | EvalExecution]]


class EvaluationRunner:
    """Run golden cases sequentially through an injected async callable."""

    def __init__(self, run_case: RunCase, *, fail_fast: bool = False) -> None:
        self._run_case = run_case
        self._fail_fast = fail_fast

    async def run(self, cases: Iterable[EvalCase]) -> EvaluationReport:
        """Evaluate cases in dataset order with per-case exception isolation."""

        dataset = validate_golden_dataset(tuple(cases))
        results: list[EvalCaseResult] = []
        for case in dataset:
            started = time.perf_counter()
            try:
                execution = _normalize_execution(await self._run_case(case))
                elapsed_ms = (
                    execution.latency_ms
                    if execution.latency_ms is not None
                    else _elapsed_ms(started)
                )
                result = _evaluate_case(case, execution, elapsed_ms)
            except Exception as exc:
                result = _failed_case_result(case, _elapsed_ms(started), exc)
            results.append(result)
            if self._fail_fast and not result.success:
                break
        return _build_report(tuple(results))

    async def evaluate(self, cases: Iterable[EvalCase]) -> EvaluationReport:
        """Descriptive alias for :meth:`run`."""

        return await self.run(cases)


def _normalize_execution(
    execution: AgentRunResult | EvalExecution,
) -> EvalExecution:
    """Adapt the runtime result or a deterministic test double."""

    if isinstance(execution, EvalExecution):
        return execution
    if isinstance(execution, AgentRunResult):
        return EvalExecution.from_agent_run_result(execution)
    raise TypeError("run_case must return AgentRunResult")


def _evaluate_case(
    case: EvalCase,
    execution: EvalExecution,
    latency_ms: float,
) -> EvalCaseResult:
    """Apply answer and tool expectations to one normalized execution."""

    answer_correct = _answer_matches(case, execution.answer)
    tool_correct = _tools_match(case, execution.tool_sequence)
    runtime_succeeded = execution.status == "completed" and execution.error is None
    success = runtime_succeeded and answer_correct and tool_correct is not False
    return EvalCaseResult(
        case_id=case.case_id,
        status=execution.status,
        success=success,
        answer=execution.answer,
        answer_correct=answer_correct,
        actual_tools=execution.tool_sequence,
        tool_selection_correct=tool_correct,
        steps=execution.steps,
        latency_ms=latency_ms,
        token_usage=execution.token_usage,
        error=execution.error,
    )


def _failed_case_result(
    case: EvalCase,
    latency_ms: float,
    error: Exception,
) -> EvalCaseResult:
    """Convert an isolated callable exception into a failed case result."""

    return EvalCaseResult(
        case_id=case.case_id,
        status="failed",
        success=False,
        answer=None,
        answer_correct=False,
        actual_tools=(),
        tool_selection_correct=(None if case.expected_tools is None else False),
        steps=0,
        latency_ms=latency_ms,
        token_usage=0,
        error=f"{type(error).__name__}: {error}",
    )


def _answer_matches(case: EvalCase, answer: str | None) -> bool:
    """Use case-sensitive substring matching for every declared answer fragment."""

    if case.expected_answer_contains is None:
        return True
    if answer is None:
        return False
    return all(fragment in answer for fragment in case.expected_answer_contains)


def _tools_match(case: EvalCase, actual_tools: tuple[str, ...]) -> bool | None:
    """Compare the complete ordered tool sequence when one is declared."""

    if case.expected_tools is None:
        return None
    return actual_tools == case.expected_tools


def _elapsed_ms(started: float) -> float:
    """Return monotonic elapsed time in milliseconds."""

    return (time.perf_counter() - started) * 1000


def _build_report(results: tuple[EvalCaseResult, ...]) -> EvaluationReport:
    """Compute aggregate metrics, including explicit empty-batch behavior."""

    count = len(results)
    successful = sum(result.success for result in results)
    declared_tool_results = [
        result.tool_selection_correct
        for result in results
        if result.tool_selection_correct is not None
    ]
    total_tokens = sum(result.token_usage for result in results)
    summary = EvaluationSummary(
        case_count=count,
        successful_cases=successful,
        task_success_rate=(successful / count if count else 0.0),
        tool_selection_accuracy=(
            sum(value is True for value in declared_tool_results)
            / len(declared_tool_results)
            if declared_tool_results
            else None
        ),
        tool_selection_case_count=len(declared_tool_results),
        average_steps=(
            sum(result.steps for result in results) / count if count else 0.0
        ),
        p95_latency_ms=_percentile(
            [result.latency_ms for result in results],
            percentile=0.95,
        ),
        total_tokens=total_tokens,
        average_tokens=(total_tokens / count if count else 0.0),
    )
    return EvaluationReport(results=results, summary=summary)


def _percentile(values: list[float], *, percentile: float) -> float:
    """Compute an interpolated percentile; empty input returns ``0.0``."""

    if not values:
        return 0.0
    if not 0 <= percentile <= 1:
        raise ValueError("percentile must be between zero and one")
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction
