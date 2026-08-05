from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

import pytest
from fastapi.testclient import TestClient

from app.agents.models import (
    AgentDecision,
    AgentRunResult,
    AgentState,
    AgentStep,
    RunStatus,
    StopReason,
    ToolCall,
    ToolResult,
)
from app.api.agent import _safe_error_message, _to_rag_summary
from app.auth.models import APIKey
from app.core.context import RequestContext
from app.main import app
from app.schemas.agent import AgentRAGToolSummary, AgentRunRequest
from app.services.agent_service import AgentRunOutcome, get_agent_service

client = TestClient(app)
_AUTH_HEADERS = {"Authorization": "Bearer sk-test-integration"}


@dataclass
class _FakeAgentService:
    outcome: AgentRunOutcome

    async def run(
        self,
        request: AgentRunRequest,
        *,
        context: RequestContext,
        api_key: APIKey,
    ) -> AgentRunOutcome:
        del request, context, api_key
        return self.outcome


def _rag_summary(payload: Mapping[str, object]) -> AgentRAGToolSummary:
    return _to_rag_summary(json.dumps(payload), output_truncated=False)


def _outcome(steps: list[AgentStep]) -> AgentRunOutcome:
    run_id = "run-rag-contract"
    state = AgentState(run_id=run_id, user_input="hello")
    state.steps.extend(steps)
    return AgentRunOutcome(
        result=AgentRunResult(
            run_id=run_id,
            status=RunStatus.COMPLETED,
            stop_reason=StopReason.DIRECT_ANSWER,
            answer="safe answer",
            state=state,
            events=(),
            token_usage=0,
        ),
        model="test-model",
        prompt_tokens=1,
        completion_tokens=1,
        estimated_usage=False,
    )


def _override(outcome: AgentRunOutcome) -> None:
    app.dependency_overrides[get_agent_service] = lambda: _FakeAgentService(outcome)


def _clear_override() -> None:
    app.dependency_overrides.pop(get_agent_service, None)


def _knowledge_result(
    call_id: str,
    payload: Mapping[str, object],
    *,
    truncated: bool = False,
) -> ToolResult:
    return ToolResult(
        call_id=call_id,
        name="knowledge_search",
        content=json.dumps(payload),
        succeeded=not bool(payload.get("ok") is False),
        error=None
        if payload.get("ok") is not False
        else str(payload.get("error_code")),
        truncated=truncated,
    )


def test_rag_summary_exposes_two_sources() -> None:
    summary = _rag_summary(
        {
            "ok": True,
            "results": [
                {
                    "document_id": "doc-1",
                    "chunk_id": "chunk-1",
                    "chunk_index": 0,
                    "content": "first source",
                    "distance": 0.12,
                },
                {
                    "document_id": "doc-2",
                    "chunk_id": "chunk-2",
                    "chunk_index": 1,
                    "content": "second source",
                    "distance": 0.34,
                },
            ],
        }
    )

    assert summary.status == "success_with_sources"
    assert len(summary.references) == 2
    assert [reference.document_id for reference in summary.references] == [
        "doc-1",
        "doc-2",
    ]
    assert [reference.content for reference in summary.references] == [
        "first source",
        "second source",
    ]
    assert summary.warning is not None


def test_rag_summary_empty_results_are_no_relevant_sources() -> None:
    summary = _rag_summary({"ok": True, "results": []})

    assert summary.status == "no_relevant_sources"
    assert summary.references == []


@pytest.mark.parametrize(
    ("error_code", "expected_status"),
    [
        ("knowledge_base_empty", "knowledge_base_empty"),
        ("rag_storage_unavailable", "rag_unavailable"),
        ("embedding_unavailable", "rag_unavailable"),
        ("embedding_failed", "embedding_failed"),
        ("rag_unavailable", "rag_unavailable"),
    ],
)
def test_rag_summary_maps_known_error_codes_to_public_statuses(
    error_code: str,
    expected_status: str,
) -> None:
    summary = _rag_summary({"ok": False, "results": [], "error_code": error_code})

    assert summary.status == expected_status
    assert summary.error_code == error_code
    assert summary.references == []


def test_rag_summary_rejects_unknown_error_code_and_untrusted_warning() -> None:
    summary = _rag_summary(
        {
            "ok": False,
            "results": [],
            "error_code": "internal_secret_code",
            "warning": "Traceback /Users/private key=sk-secret",
            "message": "provider response",
        }
    )

    assert summary.status == "failed"
    assert summary.error_code == "failed"
    assert summary.warning != "Traceback /Users/private key=sk-secret"
    assert "secret" not in json.dumps(summary.model_dump())


def test_rag_reference_missing_distance_stays_none() -> None:
    summary = _rag_summary(
        {
            "ok": True,
            "results": [
                {
                    "document_id": "doc-without-distance",
                    "chunk_id": "chunk-without-distance",
                    "chunk_index": 3,
                    "content": "source without distance",
                }
            ],
        }
    )

    assert len(summary.references) == 1
    assert summary.references[0].distance is None


def test_rag_summary_drops_reference_without_stable_identifier() -> None:
    summary = _rag_summary(
        {
            "ok": True,
            "results": [
                {
                    "chunk_index": 3,
                    "content": "content without a document or chunk identifier",
                    "distance": 0.12,
                }
            ],
        }
    )

    assert summary.status == "no_relevant_sources"
    assert summary.references == []


def test_rag_reference_long_content_is_limited_to_1200_characters() -> None:
    long_content = "x" * 1201

    summary = _rag_summary(
        {
            "ok": True,
            "results": [
                {
                    "document_id": "doc-long",
                    "chunk_id": "chunk-long",
                    "chunk_index": 4,
                    "content": long_content,
                    "distance": 0.01,
                }
            ],
        }
    )

    reference = summary.references[0]
    assert reference.content == "x" * 1200
    assert reference.truncated is True


def test_rag_reference_content_redacts_sensitive_patterns_and_keeps_plain_text() -> (
    None
):
    summary = _rag_summary(
        {
            "ok": True,
            "results": [
                {
                    "document_id": "doc-safe-cleaning",
                    "chunk_id": "chunk-safe-cleaning",
                    "chunk_index": 0,
                    "content": (
                        "Keep this ordinary sentence. "
                        "api_key=sk-test-secret-token "
                        "Bearer abc.def.ghi\n"
                        "Traceback (most recent call last):\n"
                        '  File "/Users/Admin/project/app.py", line 10, in <module>\n'
                        "See /private/secret/config.env for details."
                    ),
                    "distance": 0.12,
                }
            ],
        }
    )

    reference = summary.references[0]
    assert reference.content is not None
    assert "Keep this ordinary sentence." in reference.content
    assert "sk-test-secret-token" not in reference.content
    assert "Bearer abc.def.ghi" not in reference.content
    assert "Traceback" not in reference.content
    assert "/Users/Admin/project/app.py" not in reference.content
    assert "/private/secret/config.env" not in reference.content
    assert len(reference.content) <= 1200
    assert reference.truncated is True


def test_rag_summary_drops_malformed_reference_fields() -> None:
    summary = _rag_summary(
        {
            "ok": True,
            "results": [
                {
                    "document_id": "valid",
                    "chunk_id": "valid-chunk",
                    "chunk_index": 0,
                    "content": "valid",
                    "distance": 0.2,
                },
                {"document_id": 123, "content": "bad id"},
                {"document_id": "bad-distance", "distance": True},
                {"document_id": "bad-nan", "distance": float("nan")},
                {"document_id": "bad-index", "chunk_index": True},
                {"source": {"document_id": "must-not-be-projected"}},
            ],
        }
    )

    assert len(summary.references) == 1
    assert summary.references[0].document_id == "valid"


def test_rag_summary_uses_stable_status_for_truncated_and_malformed_output() -> None:
    truncated = _to_rag_summary("{}", output_truncated=True)
    malformed = _to_rag_summary("not-json", output_truncated=False)
    missing_results = _rag_summary({"ok": True})

    assert truncated.status == "output_unavailable"
    assert truncated.error_code == "output_truncated"
    assert truncated.references == []
    assert malformed.status == "output_unavailable"
    assert malformed.error_code == "output_malformed"
    assert missing_results.status == "output_unavailable"
    assert missing_results.error_code == "output_malformed"


def test_safe_error_message_does_not_expose_tool_result_content() -> None:
    result = ToolResult(
        call_id="call-secret",
        name="knowledge_search",
        content=(
            "Traceback (most recent call last):\n"
            "RuntimeError: database failed with api_key=sk-secret-token"
        ),
        succeeded=False,
        error="tool_execution_failed",
    )

    message = _safe_error_message(result)

    assert message == "The tool could not complete safely."
    assert message is not None
    assert "Traceback" not in message
    assert "sk-secret-token" not in message
    assert "api_key" not in message


def test_http_response_associates_multiple_rag_calls_without_raw_payloads() -> None:
    first_call = ToolCall("search-1", "knowledge_search", {"query": "secret query"})
    second_call = ToolCall("search-2", "knowledge_search", {"query": "other query"})
    first_step = AgentStep(
        index=1,
        decision=AgentDecision(tool_calls=(first_call,)),
        tool_results=(
            _knowledge_result(
                "search-1",
                {
                    "ok": True,
                    "query": "secret query",
                    "warning": "internal warning",
                    "results": [
                        {
                            "document_id": "doc-1",
                            "chunk_id": "chunk-1",
                            "chunk_index": 0,
                            "content": "source one",
                            "distance": 0.1,
                            "source": {"absolute_path": "/private/secret.txt"},
                        }
                    ],
                },
            ),
        ),
    )
    second_step = AgentStep(
        index=2,
        decision=AgentDecision(tool_calls=(second_call,)),
        tool_results=(
            _knowledge_result(
                "search-2",
                {
                    "ok": True,
                    "query": "other query",
                    "results": [
                        {
                            "document_id": "doc-2",
                            "chunk_id": "chunk-2",
                            "chunk_index": 1,
                            "content": "source two",
                            "distance": 0.2,
                        }
                    ],
                },
            ),
        ),
    )
    _override(_outcome([first_step, second_step]))
    try:
        response = client.post(
            "/api/v1/agent/runs",
            json={"message": "hello"},
            headers=_AUTH_HEADERS,
        )
    finally:
        _clear_override()

    assert response.status_code == 200
    body = response.json()
    assert body["steps"][0]["tool_calls"][0]["call_id"] == "search-1"
    assert (
        body["steps"][0]["tool_calls"][0]["rag"]["references"][0]["document_id"]
        == "doc-1"
    )
    assert body["steps"][1]["tool_calls"][0]["call_id"] == "search-2"
    assert (
        body["steps"][1]["tool_calls"][0]["rag"]["references"][0]["document_id"]
        == "doc-2"
    )

    def keys(value: object) -> set[str]:
        if isinstance(value, dict):
            return set(value) | {key for item in value.values() for key in keys(item)}
        if isinstance(value, list):
            return {key for item in value for key in keys(item)}
        return set()

    assert not {"arguments", "query", "message", "source"} & keys(body)
    encoded = json.dumps(body)
    for forbidden in ("/private/secret.txt", "Traceback", "sk-secret"):
        assert forbidden not in encoded
    assert "source one" in encoded


def test_http_response_preserves_old_fields_and_calculator_contract() -> None:
    call = ToolCall("calculator-1", "calculator", {"expression": "1+1"})
    step = AgentStep(
        index=1,
        decision=AgentDecision(tool_calls=(call,)),
        tool_results=(
            ToolResult(
                call_id="calculator-1",
                name="calculator",
                content='{"ok":false,"error":"division by zero"}',
                succeeded=False,
                error="tool_execution_failed",
            ),
        ),
    )
    _override(_outcome([step]))
    try:
        response = client.post(
            "/api/v1/agent/runs",
            json={"message": "calculate"},
            headers=_AUTH_HEADERS,
        )
    finally:
        _clear_override()

    assert response.status_code == 200
    body = response.json()
    step_body = body["steps"][0]
    assert step_body["index"] == 1
    assert step_body["decision_kind"] == "tool_call"
    assert step_body["tool_names"] == ["calculator"]
    assert step_body["tool_succeeded"] is False
    assert step_body["tool_calls"][0]["error_code"] == "tool_execution_failed"
    assert step_body["tool_calls"][0]["error_message"] == (
        "The tool could not complete safely."
    )
    assert "rag" not in step_body["tool_calls"][0]

    old_client_fields = {
        "run_id",
        "status",
        "answer",
        "stop_reason",
        "steps",
        "events",
        "usage",
    }
    assert old_client_fields.issubset(body)


def _post_single_tool_result(result: ToolResult) -> dict[str, object]:
    call = ToolCall(result.call_id, result.name, {"query": "private query"})
    step = AgentStep(
        index=1,
        decision=AgentDecision(tool_calls=(call,)),
        tool_results=(result,),
    )
    _override(_outcome([step]))
    try:
        response = client.post(
            "/api/v1/agent/runs",
            json={"message": "hello"},
            headers=_AUTH_HEADERS,
        )
    finally:
        _clear_override()
    assert response.status_code == 200
    return cast(dict[str, object], response.json())


def test_http_response_redacts_sensitive_rag_content_but_keeps_plain_text() -> None:
    body = _post_single_tool_result(
        _knowledge_result(
            "sensitive-content",
            {
                "ok": True,
                "results": [
                    {
                        "document_id": "doc-http-cleaning",
                        "chunk_id": "chunk-http-cleaning",
                        "chunk_index": 0,
                        "content": (
                            "Keep this ordinary sentence. "
                            "api_key=sk-http-secret-token "
                            "Bearer http.token.value\n"
                            "Traceback (most recent call last):\n"
                            '  File "/Users/Admin/project/app.py", line 10\n'
                            "See /private/secret/config.env."
                        ),
                    }
                ],
            },
        )
    )

    encoded = json.dumps(body)
    assert "Keep this ordinary sentence." in encoded
    for forbidden in (
        "api_key=sk-http-secret-token",
        "sk-http-secret-token",
        "Bearer http.token.value",
        "Traceback",
        "/Users/Admin/project/app.py",
        "/private/secret/config.env",
    ):
        assert forbidden not in encoded


@pytest.mark.parametrize(
    ("result", "expected_status", "expected_error"),
    [
        (
            _knowledge_result(
                "empty",
                {"ok": False, "results": [], "error_code": "knowledge_base_empty"},
            ),
            "knowledge_base_empty",
            "knowledge_base_empty",
        ),
        (
            _knowledge_result(
                "storage",
                {
                    "ok": False,
                    "results": [],
                    "error_code": "rag_storage_unavailable",
                },
            ),
            "rag_unavailable",
            "rag_storage_unavailable",
        ),
        (
            _knowledge_result(
                "embedding",
                {"ok": False, "results": [], "error_code": "embedding_failed"},
            ),
            "embedding_failed",
            "embedding_failed",
        ),
        (
            ToolResult(
                call_id="truncated",
                name="knowledge_search",
                content='{"ok":true,"results":[]}',
                succeeded=True,
                truncated=True,
            ),
            "output_unavailable",
            "output_truncated",
        ),
        (
            ToolResult(
                call_id="malformed",
                name="knowledge_search",
                content="not-json",
                succeeded=True,
            ),
            "output_unavailable",
            "output_malformed",
        ),
    ],
)
def test_http_response_exposes_stable_rag_statuses(
    result: ToolResult,
    expected_status: str,
    expected_error: str,
) -> None:
    body = _post_single_tool_result(result)

    steps = cast(list[dict[str, object]], body["steps"])
    tool_calls = cast(list[dict[str, object]], steps[0]["tool_calls"])
    rag = tool_calls[0]["rag"]
    assert isinstance(rag, dict)
    assert rag["status"] == expected_status
    assert rag["error_code"] == expected_error
    assert rag["references"] == []


def test_http_response_keeps_calculator_success_without_rag() -> None:
    body = _post_single_tool_result(
        ToolResult(
            call_id="calculator-success",
            name="calculator",
            content='{"ok":true,"result":2}',
            succeeded=True,
        )
    )

    steps = cast(list[dict[str, object]], body["steps"])
    tool_calls = cast(list[dict[str, object]], steps[0]["tool_calls"])
    tool_call = tool_calls[0]
    assert tool_call["name"] == "calculator"
    assert tool_call["succeeded"] is True
    assert "rag" not in tool_call
