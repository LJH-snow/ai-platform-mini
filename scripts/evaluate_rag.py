"""Evaluate RAG retrieval quality against golden JSONL data.

Usage:
    python scripts/evaluate_rag.py tests/fixtures/evals/rag_golden.jsonl \
        --owner-key-hash <64-hex-sha256> \
        --output output/rag_eval_report

Requires RAG_ENABLED=true, PostgreSQL/pgvector, and an Ollama embedding
service. The script never prints API keys, full document content, or
internal stack traces.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.core.container import (
    provide_embedder,
    provide_rag_service,
    provide_vector_store,
)
from app.core.settings import get_settings
from app.db.init import dispose_db, init_db
from app.evals.jsonl import GoldenDatasetError
from app.evals.memory_repository import InMemoryRAGEvaluationRepository
from app.evals.postgres_repository import PostgresRAGEvaluationRepository
from app.evals.rag_jsonl import read_rag_golden_dataset
from app.evals.rag_models import RAGReport
from app.evals.rag_runner import RAGEvaluationRunner
from app.evals.repository import RAGEvaluationRun
from app.evals.retrievers import (
    EmbeddingVectorStoreRetriever,
    RAGServiceRetriever,
)
from app.rag.ollama_embedder import OllamaEmbedder
from app.rag.vector_store import validate_owner_key_hash


async def run_evaluation(args: argparse.Namespace) -> int:
    """Run a real retrieval evaluation and hide all internal failure details."""

    try:
        return await _run_evaluation(args)
    except Exception:
        print(
            "RAG evaluation failed; no internal details are printed.",
            file=sys.stderr,
        )
        return 1


async def _run_evaluation(args: argparse.Namespace) -> int:
    """Execute the evaluation and write JSON/Markdown reports."""

    settings = get_settings()
    if not settings.rag_enabled:
        print(
            "RAG_ENABLED=false; set RAG_ENABLED=true to run a real RAG evaluation.",
            file=sys.stderr,
        )
        return 1

    database_url = settings.database_url.get_secret_value()
    if not database_url.startswith("postgresql+asyncpg://"):
        print(
            "RAG evaluation requires a PostgreSQL asyncpg database_url.",
            file=sys.stderr,
        )
        return 1

    try:
        validate_owner_key_hash(args.owner_key_hash)
    except ValueError as exc:
        print(f"Invalid --owner-key-hash: {exc}", file=sys.stderr)
        return 1

    try:
        cases = read_rag_golden_dataset(Path(args.dataset))
    except (GoldenDatasetError, OSError, UnicodeDecodeError) as exc:
        print(f"Dataset error: {exc}", file=sys.stderr)
        return 1
    if not cases:
        print("Dataset contains no RAG cases.", file=sys.stderr)
        return 1

    embedder: OllamaEmbedder | None = None
    db_initialized = False
    with _suppress_internal_logging():
        try:
            await init_db(database_url, echo=settings.debug, include_rag=True)
            db_initialized = True

            embedder = provide_embedder()
            vector_store = provide_vector_store()
            if embedder is None or vector_store is None:
                print(
                    "Could not initialize the RAG embedder/vector store.",
                    file=sys.stderr,
                )
                return 1

            retriever: EmbeddingVectorStoreRetriever | RAGServiceRetriever
            if args.retriever == "service":
                rag_service = provide_rag_service()
                if rag_service is None:
                    print("Could not initialize the RAG service.", file=sys.stderr)
                    return 1
                retriever = RAGServiceRetriever(
                    rag_service,
                    args.owner_key_hash,
                )
            else:
                retriever = EmbeddingVectorStoreRetriever(
                    embedder=embedder,
                    vector_store=vector_store,
                    top_k=settings.rag_top_k,
                    max_distance=settings.rag_max_distance,
                    owner_key_hash=args.owner_key_hash,
                )

            runner = RAGEvaluationRunner(lambda case: retriever.retrieve(case.query))
            report = await runner.run(cases)

            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            json_path = output_path.with_suffix(".json")
            markdown_path = output_path.with_suffix(".md")
            json_path.write_text(report.to_json(indent=2), encoding="utf-8")
            markdown_path.write_text(
                _build_markdown(
                    report,
                    dataset=str(Path(args.dataset)),
                    retriever=args.retriever,
                ),
                encoding="utf-8",
            )

            await _persist_run(report, args)

            _print_summary(report, json_path, markdown_path)
            return 0
        finally:
            if embedder is not None:
                try:
                    await embedder.close()
                except Exception:
                    print("Warning: failed to close embedder.", file=sys.stderr)
            if db_initialized:
                try:
                    await dispose_db()
                except Exception:
                    print("Warning: failed to dispose database.", file=sys.stderr)


class _NoStackFilter(logging.Filter):
    """Drop log records that carry exception tracebacks."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.exc_info is None


@contextmanager
def _suppress_internal_logging() -> Iterator[None]:
    """Silence dependency logger stack traces during the evaluation."""

    logger = logging.getLogger("app.db.init")
    stack_filter = _NoStackFilter()
    logger.addFilter(stack_filter)
    try:
        yield
    finally:
        logger.removeFilter(stack_filter)


def _build_markdown(
    report: RAGReport,
    *,
    dataset: str,
    retriever: str,
) -> str:
    """Render a safe, human-readable Markdown evaluation report."""

    summary = report.summary
    lines = [
        "# RAG Evaluation Report",
        "",
        f"- Dataset: `{_escape_markdown(dataset)}`",
        f"- Retriever: `{_escape_markdown(retriever)}`",
        f"- Cases: {summary.case_count}",
        (
            "- Retrieval success rate: "
            f"{_format_percent(summary.retrieval_success_rate)}"
        ),
        f"- Context recall@k: {_format_metric(summary.context_recall_at_k)}",
        f"- Document recall@k: {_format_metric(summary.document_recall_at_k)}",
        f"- Chunk recall@k: {_format_metric(summary.chunk_recall_at_k)}",
        (
            "- Answer correctness accuracy: "
            f"{_format_metric(summary.answer_correctness_accuracy)}"
        ),
        (f"- Average retrieved chunks: {summary.average_retrieved_chunks:.3f}"),
        f"- p95 latency: {summary.p95_latency_ms:.1f} ms",
        "",
        "## Case Results",
        "",
        "| case_id | status | retrieved | recall@k | doc recall | "
        "chunk recall | answer | error |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for result in report.results:
        lines.append(
            "| "
            f"{_escape_markdown(result.case_id)} | "
            f"{_escape_markdown(result.status)} | {result.retrieved_count} | "
            f"{_format_metric(result.context_recall_at_k)} | "
            f"{_format_metric(result.document_recall_at_k)} | "
            f"{_format_metric(result.chunk_recall_at_k)} | "
            f"{_format_answer(result.answer_correct)} | "
            f"{_escape_markdown(result.error) if result.error else '--'} |"
        )

    lines.extend(["", "## Case Details", ""])
    for result in report.results:
        lines.append(f"### {_escape_markdown(result.case_id)}")
        lines.append(f"- Status: `{_escape_markdown(result.status)}`")
        lines.append(f"- Expected chunks: `{_format_ids(result.expected_chunk_ids)}`")
        lines.append(f"- Retrieved chunks: `{_format_ids(result.retrieved_chunk_ids)}`")
        lines.append(
            f"- Expected documents: `{_format_ids(result.expected_document_ids)}`"
        )
        lines.append(
            f"- Retrieved documents: `{_format_ids(result.retrieved_document_ids)}`"
        )
        if result.error is not None:
            lines.append(f"- Error: `{_escape_markdown(result.error)}`")
        lines.append("")
    return "\n".join(lines) + "\n"


def _format_metric(value: float | None) -> str:
    """Format a metric value or a stable placeholder."""

    return "--" if value is None else f"{value:.3f}"


def _format_percent(value: float) -> str:
    """Format a rate as a percentage."""

    return f"{value * 100:.1f}%"


def _format_answer(value: bool | None) -> str:
    """Format the optional answer-correctness result."""

    if value is None:
        return "--"
    return "yes" if value else "no"


def _format_ids(ids: tuple[str, ...]) -> str:
    """Render IDs without revealing document content."""

    return _escape_markdown(",".join(ids)) if ids else "--"


def _escape_markdown(value: str) -> str:
    """Escape characters that could break Markdown tables or code spans."""

    return (
        value.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("`", "'")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )


async def _persist_run(report: RAGReport, args: argparse.Namespace) -> None:
    """Write an evaluation run record when PostgreSQL is available."""

    try:
        from app.core.container import provide_session_factory

        session_factory = provide_session_factory()
        repository: (
            PostgresRAGEvaluationRepository | InMemoryRAGEvaluationRepository
        ) = PostgresRAGEvaluationRepository(session_factory)
    except Exception:
        repository = InMemoryRAGEvaluationRepository()

    summary = report.summary
    run = RAGEvaluationRun(
        id=str(uuid.uuid4()),
        dataset=str(Path(args.dataset)),
        retriever=args.retriever,
        model=None,
        case_count=summary.case_count,
        retrieval_success_rate=summary.retrieval_success_rate,
        context_recall_at_k=summary.context_recall_at_k,
        document_recall_at_k=summary.document_recall_at_k,
        chunk_recall_at_k=summary.chunk_recall_at_k,
        answer_correctness_accuracy=summary.answer_correctness_accuracy,
        answer_correctness_case_count=summary.answer_correctness_case_count,
        average_retrieved_chunks=summary.average_retrieved_chunks,
        p95_latency_ms=summary.p95_latency_ms,
    )
    try:
        await repository.save(run)
    except Exception:
        pass


def _print_summary(
    report: RAGReport,
    json_path: Path,
    markdown_path: Path,
) -> None:
    """Print only aggregate results and output paths."""

    summary = report.summary
    recall = _format_metric(summary.context_recall_at_k)
    print(
        f"RAG evaluation complete: {summary.case_count} cases, "
        f"retrieval success {summary.retrieval_success_count}/"
        f"{summary.case_count}, context recall@k {recall}."
    )
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {markdown_path}")


def main() -> None:
    """Parse CLI arguments and run the evaluation."""

    parser = argparse.ArgumentParser(
        description="Evaluate RAG retrieval against golden JSONL data"
    )
    parser.add_argument("dataset", help="Path to RAG golden JSONL")
    parser.add_argument(
        "--owner-key-hash",
        required=True,
        help="SHA-256 hex hash of the tenant API key",
    )
    parser.add_argument(
        "--output",
        default="output/rag_eval_report",
        help="Report base path (writes .json and .md)",
    )
    parser.add_argument(
        "--retriever",
        choices=("embedding", "service"),
        default="embedding",
        help="Retrieval adapter: embedding (default) or service",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(run_evaluation(args)))


if __name__ == "__main__":
    main()
