from __future__ import annotations

import argparse
import logging
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from app.evals.rag_models import (
    RAGEvalCaseResult,
    RAGReport,
    RAGSummary,
    RetrievalOutcome,
    RetrievalReference,
)
from scripts import evaluate_rag

_FIXTURE = Path("tests/fixtures/evals/rag_golden.jsonl")
_OWNER = "a" * 64


class _FakeEmbedder:
    async def close(self) -> None:
        return None


class _FakeRetriever:
    def __init__(self, **kwargs: object) -> None:
        return None

    async def retrieve(self, query: str) -> RetrievalOutcome:
        return RetrievalOutcome(
            references=(
                RetrievalReference(
                    document_id="doc-policy",
                    chunk_id="chunk-refund-01",
                    chunk_index=0,
                    distance=0.1,
                ),
            ),
            status="success",
        )


class _FakeSettings:
    def __init__(self) -> None:
        self.rag_enabled = True
        self.database_url = SecretStr(
            "postgresql+asyncpg://user:pass@localhost:5432/db"
        )
        self.debug = False
        self.rag_top_k = 5
        self.rag_max_distance = 0.9


def _make_args(dataset: Path, output: Path) -> argparse.Namespace:
    return argparse.Namespace(
        dataset=str(dataset),
        owner_key_hash=_OWNER,
        output=str(output),
        retriever="embedding",
    )


@pytest.mark.asyncio
async def test_rag_script_runs_with_string_dataset_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(evaluate_rag, "get_settings", _FakeSettings)
    monkeypatch.setattr(evaluate_rag, "init_db", AsyncMock())
    monkeypatch.setattr(evaluate_rag, "dispose_db", AsyncMock())
    monkeypatch.setattr(evaluate_rag, "provide_embedder", lambda: _FakeEmbedder())
    monkeypatch.setattr(evaluate_rag, "provide_vector_store", lambda: object())
    monkeypatch.setattr(
        evaluate_rag,
        "EmbeddingVectorStoreRetriever",
        _FakeRetriever,
    )

    return_code = await evaluate_rag.run_evaluation(
        _make_args(_FIXTURE, tmp_path / "report")
    )

    assert return_code == 0
    assert (tmp_path / "report.json").is_file()
    assert (tmp_path / "report.md").is_file()


@pytest.mark.asyncio
async def test_rag_script_hides_non_utf8_dataset_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(evaluate_rag, "get_settings", _FakeSettings)
    bad_dataset = tmp_path / "bad.jsonl"
    bad_dataset.write_bytes(b"\xff\xfe\x00\x01")

    return_code = await evaluate_rag.run_evaluation(
        _make_args(bad_dataset, tmp_path / "report")
    )
    captured = capsys.readouterr()

    assert return_code == 1
    assert "Dataset error" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.asyncio
async def test_rag_script_hides_unexpected_settings_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def failing_settings() -> _FakeSettings:
        raise RuntimeError("settings exploded")

    monkeypatch.setattr(evaluate_rag, "get_settings", failing_settings)

    return_code = await evaluate_rag.run_evaluation(
        _make_args(_FIXTURE, tmp_path / "report")
    )
    captured = capsys.readouterr()

    assert return_code == 1
    assert "RAG evaluation failed" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.asyncio
async def test_rag_script_suppresses_dependency_logger_stack_traces(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    dependency_logger = logging.getLogger("app.db.init")

    async def failing_init_db(*args: object, **kwargs: object) -> None:
        dependency_logger.exception("simulated dependency failure")
        raise RuntimeError("db init failed")

    monkeypatch.setattr(evaluate_rag, "get_settings", _FakeSettings)
    monkeypatch.setattr(evaluate_rag, "init_db", failing_init_db)

    with caplog.at_level(logging.ERROR, logger="app.db.init"):
        return_code = await evaluate_rag.run_evaluation(
            _make_args(_FIXTURE, tmp_path / "report")
        )
    captured = capsys.readouterr()

    assert return_code == 1
    assert "RAG evaluation failed" in captured.err
    assert "Traceback" not in captured.err
    assert not [
        record
        for record in caplog.records
        if record.getMessage() == "simulated dependency failure"
    ]


def _sample_summary() -> RAGSummary:
    return RAGSummary(
        case_count=1,
        retrieval_success_count=1,
        retrieval_success_rate=1.0,
        context_recall_at_k=0.0,
        document_recall_at_k=None,
        chunk_recall_at_k=None,
        answer_correctness_accuracy=None,
        answer_correctness_case_count=0,
        average_retrieved_chunks=0.0,
        p95_latency_ms=1.0,
    )


def test_rag_script_markdown_escapes_user_fields() -> None:
    result = RAGEvalCaseResult(
        case_id="a|b\n`c`\\d\r",
        status="success",
        success=True,
        expected_document_ids=("d|1",),
        expected_chunk_ids=("c|1\n`x`",),
        retrieved_document_ids=("r|1",),
        retrieved_chunk_ids=("r|1\n`y`",),
        retrieved_count=1,
        document_recall_at_k=0.0,
        chunk_recall_at_k=0.0,
        context_recall_at_k=0.0,
        answer_correct=None,
        top_k=None,
        latency_ms=1.0,
        error="boom|err\n`e`\r",
    )

    markdown = evaluate_rag._build_markdown(
        RAGReport(results=(result,), summary=_sample_summary()),
        dataset="p | q\n`r`\r",
        retriever="embedding",
    )

    assert "a\\|b" in markdown
    assert "c\\|1" in markdown
    assert "d\\|1" in markdown
    assert "boom\\|err" in markdown
    assert "p \\| q" in markdown
    assert "\\n" in markdown
    assert "\\r" in markdown
    assert "`c`" not in markdown
    assert "`x`" not in markdown
