from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from app.evals import (
    EvalCase,
    EvalExecution,
    EvaluationRunner,
    GoldenDatasetError,
    golden_dataset_to_jsonl,
    read_golden_dataset,
    write_golden_dataset,
)

_FIXTURE = Path("tests/fixtures/evals/agent_golden.jsonl")


def test_jsonl_round_trip_is_stable_and_preserves_optional_expectations() -> None:
    cases = (
        EvalCase(
            case_id="tool-case",
            input="calculate",
            expected_answer_contains=("42", "answer"),
            expected_tools=("calculator",),
            metadata={"nested": {"ok": True}, "label": "离线"},
            max_steps=3,
        ),
        EvalCase(
            case_id="no-expectations",
            input="say hello",
            expected_tools=(),
        ),
    )

    first = golden_dataset_to_jsonl(cases)
    restored = read_golden_dataset(first)
    second = golden_dataset_to_jsonl(restored)

    assert restored == cases
    assert second == first
    assert json.loads(first.splitlines()[0])["metadata"]["label"] == "离线"


def test_jsonl_file_and_stream_io_are_supported(tmp_path: Path) -> None:
    cases = (EvalCase(case_id="one", input="hello"),)
    destination = tmp_path / "golden.jsonl"

    write_golden_dataset(cases, destination)
    assert read_golden_dataset(destination) == cases

    stream = io.StringIO()
    write_golden_dataset(cases, stream)
    assert read_golden_dataset(stream.getvalue()) == cases


def test_dataset_validation_rejects_malformed_or_duplicate_cases() -> None:
    invalid_payloads = (
        '{"case_id":"","input":"x"}',
        '{"case_id":"x","input":""}',
        '{"case_id":"x","input":"x","max_steps":0}',
        '{"case_id":"x","input":"x","max_steps":-1}',
        '{"case_id":"x","input":"x","metadata":{"bad": {"set": NaN}}}',
        '{"case_id":"x","input":"x","unexpected":true}',
        "__import__('os').system('touch /tmp/should-not-run')",
    )

    for payload in invalid_payloads:
        with pytest.raises(GoldenDatasetError):
            read_golden_dataset(payload)

    duplicate = '{"case_id":"same","input":"one"}\n{"case_id":"same","input":"two"}\n'
    with pytest.raises(GoldenDatasetError, match="duplicate case_id"):
        read_golden_dataset(duplicate)


def test_case_constructor_normalizes_string_expectations_and_rejects_bad_values() -> (
    None
):
    case = EvalCase(
        case_id="case",
        input="question",
        expected_answer_contains="answer",
        expected_tools="calculator",
    )

    assert case.expected_answer_contains == ("answer",)
    assert case.expected_tools == ("calculator",)
    assert case.has_expected_tools is True

    with pytest.raises(ValueError, match="max_steps"):
        EvalCase(case_id="bad", input="x", max_steps=-1)
    with pytest.raises(ValueError, match="tool_sequence"):
        EvalExecution(tool_sequence=("",))


@pytest.mark.asyncio
async def test_runner_checks_answer_and_ordered_tool_selection() -> None:
    cases = (
        EvalCase(
            case_id="correct",
            input="correct",
            expected_answer_contains=("needle",),
            expected_tools=("knowledge_search", "calculator"),
        ),
        EvalCase(
            case_id="wrong-tool-order",
            input="wrong-tool-order",
            expected_answer_contains=("needle",),
            expected_tools=("knowledge_search", "calculator"),
        ),
        EvalCase(
            case_id="wrong-answer",
            input="wrong-answer",
            expected_answer_contains=("needle",),
            expected_tools=(),
        ),
    )

    async def run_case(case: EvalCase) -> EvalExecution:
        if case.case_id == "correct":
            return EvalExecution(
                answer="needle found",
                tool_sequence=("knowledge_search", "calculator"),
                steps=2,
                token_usage=10,
            )
        if case.case_id == "wrong-tool-order":
            return EvalExecution(
                answer="needle found",
                tool_sequence=("calculator", "knowledge_search"),
                steps=2,
                token_usage=20,
            )
        return EvalExecution(
            answer="other answer",
            tool_sequence=(),
            steps=1,
            token_usage=30,
        )

    report = await EvaluationRunner(run_case).run(cases)

    assert [result.case_id for result in report.results] == [
        "correct",
        "wrong-tool-order",
        "wrong-answer",
    ]
    assert [result.success for result in report.results] == [True, False, False]
    assert report.results[0].tool_selection_correct is True
    assert report.results[1].tool_selection_correct is False
    assert report.summary.task_success_rate == pytest.approx(1 / 3)
    assert report.summary.tool_selection_accuracy == pytest.approx(2 / 3)
    assert report.summary.tool_selection_case_count == 3


@pytest.mark.asyncio
async def test_runner_isolates_exceptions_and_keeps_dataset_order() -> None:
    cases = tuple(
        EvalCase(case_id=case_id, input=case_id)
        for case_id in ("first", "boom", "last")
    )
    called: list[str] = []

    async def run_case(case: EvalCase) -> EvalExecution:
        called.append(case.case_id)
        if case.case_id == "boom":
            raise RuntimeError("fixture failure")
        return EvalExecution(answer=case.case_id, steps=1)

    report = await EvaluationRunner(run_case).run(cases)

    assert called == ["first", "boom", "last"]
    assert [result.case_id for result in report.results] == ["first", "boom", "last"]
    assert report.results[1].success is False
    assert report.results[1].status == "failed"
    assert report.results[1].error == "RuntimeError: fixture failure"
    assert report.summary.successful_cases == 2


@pytest.mark.asyncio
async def test_runner_fail_fast_returns_only_processed_prefix() -> None:
    cases = tuple(
        EvalCase(case_id=case_id, input=case_id)
        for case_id in ("first", "boom", "last")
    )

    async def run_case(case: EvalCase) -> EvalExecution:
        if case.case_id == "boom":
            raise RuntimeError("stop")
        return EvalExecution(answer=case.case_id)

    report = await EvaluationRunner(run_case, fail_fast=True).run(cases)

    assert [result.case_id for result in report.results] == ["first", "boom"]


@pytest.mark.asyncio
async def test_runner_accepts_agent_result_and_extracts_observations() -> None:
    from app.agents.models import (
        AgentDecision,
        AgentRunResult,
        AgentState,
        AgentStep,
        RunStatus,
        StopReason,
        ToolCall,
    )

    case = EvalCase(
        case_id="runtime",
        input="use calculator",
        expected_answer_contains=("done",),
        expected_tools=("calculator",),
    )

    async def run_case(_: EvalCase) -> AgentRunResult:
        state = AgentState(run_id="run", user_input=case.input)
        state.steps.append(
            AgentStep(
                index=1,
                decision=AgentDecision(
                    tool_calls=(ToolCall(call_id="call", name="calculator"),),
                ),
            )
        )
        return AgentRunResult(
            run_id="run",
            status=RunStatus.COMPLETED,
            stop_reason=StopReason.DIRECT_ANSWER,
            answer="done",
            state=state,
            events=(),
            token_usage=7,
        )

    report = await EvaluationRunner(run_case).run((case,))
    result = report.results[0]

    assert result.success is True
    assert result.actual_tools == ("calculator",)
    assert result.steps == 1
    assert result.token_usage == 7


@pytest.mark.asyncio
async def test_fixture_loads_and_completes_with_deterministic_fake_runner() -> None:
    cases = read_golden_dataset(_FIXTURE)

    async def run_case(case: EvalCase) -> EvalExecution:
        category = case.metadata["category"]
        if category == "direct-answer":
            answer = str(case.metadata["expected_behavior"]).removeprefix("answer ")
            return EvalExecution(answer=answer, steps=1, token_usage=2)
        if category == "calculator":
            expression = str(case.metadata["expression"])
            left, right = (int(part.strip()) for part in expression.split("+"))
            return EvalExecution(
                answer=str(left + right),
                tool_sequence=("calculator",),
                steps=2,
                token_usage=4,
            )
        index = case.case_id.rsplit("-", maxsplit=1)[-1]
        return EvalExecution(
            answer=f"knowledge-{int(index)}",
            tool_sequence=("knowledge_search",),
            steps=2,
            token_usage=5,
        )

    report = await EvaluationRunner(run_case).run(cases)

    assert len(cases) == 30
    assert report.summary.case_count == 30
    assert report.summary.task_success_rate == 1.0
    assert report.summary.tool_selection_accuracy == 1.0
    assert report.summary.total_tokens == 110
    assert report.summary.average_tokens == pytest.approx(110 / 30)


@pytest.mark.asyncio
async def test_empty_dataset_and_metric_boundaries_are_explicit() -> None:
    async def never_called(_: EvalCase) -> EvalExecution:
        raise AssertionError("empty dataset must not call run_case")

    empty = await EvaluationRunner(never_called).run(())

    assert empty.results == ()
    assert empty.summary.case_count == 0
    assert empty.summary.task_success_rate == 0.0
    assert empty.summary.tool_selection_accuracy is None
    assert empty.summary.tool_selection_case_count == 0
    assert empty.summary.average_steps == 0.0
    assert empty.summary.p95_latency_ms == 0.0
    assert empty.summary.total_tokens == 0
    assert empty.summary.average_tokens == 0.0

    cases = tuple(
        EvalCase(case_id=str(index), input=str(index)) for index in range(1, 5)
    )

    async def fixed_latency(case: EvalCase) -> EvalExecution:
        return EvalExecution(
            answer="ok",
            latency_ms=float(int(case.case_id) * 10),
            steps=int(case.case_id),
            token_usage=int(case.case_id),
        )

    report = await EvaluationRunner(fixed_latency).run(cases)

    assert report.summary.p95_latency_ms == pytest.approx(38.5)
    assert report.summary.average_steps == pytest.approx(2.5)
    assert report.summary.total_token_usage == 10
    assert report.summary.average_token_usage == pytest.approx(2.5)


@pytest.mark.asyncio
async def test_no_expected_tools_excludes_tool_accuracy_denominator() -> None:
    cases = (
        EvalCase(case_id="no-tools", input="x"),
        EvalCase(case_id="expects-none", input="y", expected_tools=()),
    )

    async def run_case(case: EvalCase) -> EvalExecution:
        return EvalExecution(
            answer="ok",
            tool_sequence=() if case.case_id == "expects-none" else ("ignored",),
        )

    report = await EvaluationRunner(run_case).run(cases)

    assert report.results[0].tool_selection_correct is None
    assert report.results[1].tool_selection_correct is True
    assert report.summary.tool_selection_accuracy == 1.0
    assert report.summary.tool_selection_case_count == 1
