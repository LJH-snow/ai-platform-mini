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

from app.core.container import provide_rag_service, provide_vector_store
from app.core.settings import Settings, get_settings
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
from app.rag.embedder import Embedder
from app.rag.ollama_embedder import OllamaEmbedder
from app.rag.vector_store import VectorStore, validate_owner_key_hash


async def run_evaluation(args: argparse.Namespace) -> int:
    """Run a real retrieval evaluation and hide all internal failure details."""

    try:
        await _validate_args(args)
        if args.compare:
            return await _run_compare(args)
        report = await _run_single(args, mode=args.search_mode)
        await _write_reports(report, args, mode=args.search_mode)
        await _persist_run(report, args, mode=args.search_mode)
        _print_summary(report, args, mode=args.search_mode)
        return 0
    except ValueError as exc:
        # Configuration/dataset errors are safe to surface by message.
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        print(
            "RAG evaluation failed; no internal details are printed.",
            file=sys.stderr,
        )
        return 1


async def _validate_args(args: argparse.Namespace) -> None:
    """Validate environment and CLI inputs, raising on any violation."""

    settings = get_settings()
    if not settings.rag_enabled:
        raise ValueError(
            "RAG_ENABLED=false; set RAG_ENABLED=true to run a real RAG evaluation."
        )
    database_url = settings.database_url.get_secret_value()
    if not database_url.startswith("postgresql+asyncpg://"):
        raise ValueError("RAG evaluation requires a PostgreSQL asyncpg database_url.")
    try:
        validate_owner_key_hash(args.owner_key_hash)
    except ValueError as exc:
        raise ValueError(f"Invalid --owner-key-hash: {exc}") from exc
    try:
        cases = read_rag_golden_dataset(Path(args.dataset))
    except (GoldenDatasetError, OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"Dataset error: {exc}") from exc
    if not cases:
        raise ValueError("Dataset contains no RAG cases.")
    if args.ingest_file is not None and not Path(args.ingest_file).is_file():
        raise ValueError(f"Corpus file not found: {args.ingest_file}")
    args.cases = cases
    args.settings = settings


async def _run_single(args: argparse.Namespace, *, mode: str) -> RAGReport:
    """Execute one evaluation pass for a search mode and return its report."""

    settings = args.settings
    cases = args.cases
    embedder: Embedder | None = None
    db_initialized = False
    with _suppress_internal_logging():
        try:
            await init_db(
                settings.database_url.get_secret_value(),
                echo=settings.debug,
                include_rag=True,
            )
            db_initialized = True

            if args.embedder == "mock":
                from app.evals.mock_embedder import MockEmbedder

                embedder = MockEmbedder(dimensions=settings.rag_embedding_dimensions)
            else:
                # Construct per pass (never reuse the lru_cache instance):
                # compare mode runs two passes and each closes its embedder.
                embedder = OllamaEmbedder(
                    base_url=settings.ollama_base_url,
                    model=settings.rag_embedding_model,
                    dimensions=settings.rag_embedding_dimensions,
                    timeout_seconds=settings.rag_embedding_timeout_seconds,
                )
            if embedder is None:
                raise ValueError("Could not initialize the RAG embedder.")

            if args.ingest_file is not None:
                await _ingest_corpus(embedder, args.ingest_file, settings, args)

            if args.retriever == "service":
                # Service path follows RAG_SEARCH_MODE from settings; the
                # mode override only applies to the embedding retriever.
                rag_service = provide_rag_service()
                if rag_service is None:
                    raise ValueError("Could not initialize the RAG service.")
                retriever: EmbeddingVectorStoreRetriever | RAGServiceRetriever = (
                    RAGServiceRetriever(
                        rag_service,
                        args.owner_key_hash,
                    )
                )
            else:
                vector_store = _build_store(mode, settings)
                if vector_store is None:
                    raise ValueError("Could not initialize the vector store.")
                retriever = EmbeddingVectorStoreRetriever(
                    embedder=embedder,
                    vector_store=vector_store,
                    top_k=settings.rag_top_k,
                    max_distance=settings.rag_max_distance,
                    owner_key_hash=args.owner_key_hash,
                )

            runner = RAGEvaluationRunner(lambda case: retriever.retrieve(case.query))
            return await runner.run(cases)
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


def _build_store(mode: str, settings: Settings) -> VectorStore | None:
    """Build the retriever store for one search mode.

    ``auto`` follows ``RAG_SEARCH_MODE`` from settings; explicit modes
    construct a fresh store so golden comparisons are hermetic.
    """

    if mode == "auto":
        return provide_vector_store()
    from typing import Literal, cast

    from app.db.session import create_async_session_factory
    from app.rag.hybrid import HybridRetriever
    from app.rag.pg_vector_store import PgVectorStore

    store = PgVectorStore(
        session_factory=create_async_session_factory(),
        embedding_model=settings.rag_embedding_model,
        embedding_dimensions=settings.rag_embedding_dimensions,
    )
    if mode == "vector":
        return store
    return HybridRetriever(
        store,
        mode=cast(Literal["hybrid", "keyword"], mode),
    )


async def _ingest_corpus(
    embedder: Embedder,
    corpus_path: str,
    settings: Settings,
    args: argparse.Namespace,
) -> None:
    """Ingest one corpus file with the active embedder before evaluation.

    Blank-line-separated paragraphs become individual chunks (the CI
    corpus format); a single paragraph falls back to ``chunk_text``.
    Re-ingesting the same corpus replaces the previous document so the
    script is idempotent for CI re-runs.
    """

    import hashlib
    import re

    from app.rag.chunker import chunk_text

    text = Path(corpus_path).read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError(f"Corpus file is empty: {corpus_path}")
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks = (
        paragraphs
        if len(paragraphs) > 1
        else chunk_text(
            text,
            chunk_size=settings.rag_chunk_size,
            overlap=settings.rag_chunk_overlap,
        )
    )
    if not chunks:
        raise ValueError(f"Corpus file produced no chunks: {corpus_path}")
    embeddings = await embedder.embed(chunks)
    store = _build_store("vector", settings)
    if store is None:
        raise ValueError("Could not initialize the ingestion vector store.")
    corpus_name = str(Path(corpus_path).name)
    existing = await store.list_documents(owner_key_hash=args.owner_key_hash)
    for document in existing:
        if document.filename == corpus_name:
            await store.delete_document(args.owner_key_hash, document.document_id)
            print(f"Replaced existing corpus document {document.document_id}.")
    await store.add_document(
        source_path=corpus_name,
        content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        embedding_model=settings.rag_embedding_model,
        embedding_dimensions=settings.rag_embedding_dimensions,
        chunks=chunks,
        embeddings=embeddings,
        owner_key_hash=args.owner_key_hash,
    )
    print(
        f"Ingested {len(chunks)} chunks from {corpus_path} ",
        "with the active embedder.",
    )


async def _run_compare(args: argparse.Namespace) -> int:
    """Run vector and hybrid modes and gate CI on retrieval regressions.

    The gate is strictly relative: hybrid must score at least as high as
    vector-only on retrieval success rate and (when content expectations
    are present) content hit rate.  No absolute thresholds.
    """

    reports: dict[str, RAGReport] = {}
    for mode in ("vector", "hybrid"):
        report = await _run_single(args, mode=mode)
        reports[mode] = report
        await _write_reports(report, args, mode=mode)
        await _persist_run(report, args, mode=mode)

    vector_summary = reports["vector"].summary
    hybrid_summary = reports["hybrid"].summary

    checks: list[tuple[bool, str, str, str]] = []
    checks.append(
        (
            hybrid_summary.retrieval_success_rate
            >= vector_summary.retrieval_success_rate,
            "retrieval_success_rate",
            _format_percent(vector_summary.retrieval_success_rate),
            _format_percent(hybrid_summary.retrieval_success_rate),
        )
    )
    if (
        hybrid_summary.content_hit_rate is not None
        and vector_summary.content_hit_rate is not None
    ):
        checks.append(
            (
                hybrid_summary.content_hit_rate >= vector_summary.content_hit_rate,
                "content_hit_rate",
                _format_percent(vector_summary.content_hit_rate),
                _format_percent(hybrid_summary.content_hit_rate),
            )
        )
    print(
        "\nComparison (vector-only -> hybrid): "
        f"retrieval_success_rate "
        f"{_format_percent(vector_summary.retrieval_success_rate)} -> "
        f"{_format_percent(hybrid_summary.retrieval_success_rate)}, "
        f"context_recall_at_k {_format_metric(vector_summary.context_recall_at_k)} "
        f"-> {_format_metric(hybrid_summary.context_recall_at_k)}"
    )
    if (
        vector_summary.content_hit_rate is not None
        and hybrid_summary.content_hit_rate is not None
    ):
        print(
            f"content_hit_rate "
            f"{_format_percent(vector_summary.content_hit_rate)} -> "
            f"{_format_percent(hybrid_summary.content_hit_rate)}"
        )

    failed = [name for ok, name, _, _ in checks if not ok]
    for ok, name, before, after in checks:
        status = "ok" if ok else "REGRESSION"
        print(f"  [{status}] {name}: {before} -> {after}")
    if failed:
        print(
            f"Golden gate FAILED: hybrid regressed on {', '.join(failed)}. ",
            file=sys.stderr,
        )
        return 1
    print("Golden gate passed: hybrid >= vector-only on all compared metrics.")
    return 0


async def _write_reports(
    report: RAGReport,
    args: argparse.Namespace,
    *,
    mode: str,
) -> None:
    """Write JSON and Markdown reports for one evaluation pass."""

    suffix = "" if mode == "auto" else f"_{mode}"
    output_path = Path(f"{args.output}{suffix}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    json_path = output_path.with_suffix(".json")
    markdown_path = output_path.with_suffix(".md")
    json_path.write_text(report.to_json(indent=2), encoding="utf-8")
    markdown_path.write_text(
        _build_markdown(
            report,
            dataset=str(Path(args.dataset)),
            retriever=f"{args.retriever}:{mode}",
        ),
        encoding="utf-8",
    )
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {markdown_path}")


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
        (
            "- Content hit rate: "
            f"{_format_metric(summary.content_hit_rate)}"
            f" ({summary.content_expected_count} content cases)"
        ),
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


async def _persist_run(
    report: RAGReport,
    args: argparse.Namespace,
    *,
    mode: str,
) -> None:
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
        retriever=f"{args.retriever}:{mode}",
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
    args: argparse.Namespace,
    *,
    mode: str,
) -> None:
    """Print only aggregate results for one evaluation pass."""

    summary = report.summary
    recall = _format_metric(summary.context_recall_at_k)
    print(
        f"RAG evaluation complete (mode={mode}): {summary.case_count} cases, "
        f"retrieval success {summary.retrieval_success_count}/"
        f"{summary.case_count}, context recall@k {recall}."
    )


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
        help="Report base path (writes .json and .md; mode suffix added)",
    )
    parser.add_argument(
        "--retriever",
        choices=("embedding", "service"),
        default="embedding",
        help="Retrieval adapter: embedding (default) or service",
    )
    parser.add_argument(
        "--embedder",
        choices=("ollama", "mock"),
        default="ollama",
        help="Embedding backend: ollama (default) or deterministic mock",
    )
    parser.add_argument(
        "--search-mode",
        choices=("auto", "vector", "hybrid", "keyword"),
        default="auto",
        help="Search mode override (default: follow RAG_SEARCH_MODE)",
    )
    parser.add_argument(
        "--ingest-file",
        default=None,
        help="Optional TXT corpus file to ingest before evaluating",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Run vector and hybrid modes; fail when hybrid regresses",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(run_evaluation(args)))


if __name__ == "__main__":
    main()
