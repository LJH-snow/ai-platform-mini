"""Run the reference LangGraph PDF report workflow from the CLI.

Usage:
    python scripts/run_pdf_workflow.py report.pdf \
        --owner-key-hash <sha256-hex> --topic "Quarterly review"

The workflow is a reference implementation and the CLI is an independent
runner. It requires ``RAG_ENABLED=true`` plus a configured LLM provider; the
stateful HTTP API is available under ``/api/v1/workflows``.
"""

import argparse
import asyncio
import json
import logging
import sys
import uuid
from pathlib import Path
from typing import cast

from langchain_core.runnables.config import RunnableConfig
from langgraph.types import Command

from app.core.container import provide_llm_provider, provide_rag_service
from app.workflows.pdf_report import (
    PdfFileExtractor,
    PDFReportState,
    PDFReportWorkflow,
    ProviderRouterReportModel,
    RagServiceReportRetriever,
    build_run_summary,
)

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a Markdown report from a PDF using the reference "
            "LangGraph workflow"
        )
    )
    parser.add_argument("pdf", type=Path, help="Path to the input PDF file")
    parser.add_argument(
        "--topic",
        default=None,
        help="Optional report topic; defaults to the first PDF text segment",
    )
    parser.add_argument(
        "--owner-key-hash",
        default=None,
        help="Lowercase SHA-256 hex hash identifying the RAG tenant",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Optional model name routed through ProviderRouter",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional Markdown report output path",
    )
    parser.add_argument(
        "--thread-id",
        default=None,
        help="Optional LangGraph thread id for checkpoint state",
    )
    parser.add_argument(
        "--no-approval",
        action="store_true",
        help="Skip the human-in-the-loop approval interrupt",
    )
    decision_group = parser.add_mutually_exclusive_group()
    decision_group.add_argument(
        "--approve",
        action="store_true",
        help="Auto-approve report generation at the interrupt",
    )
    decision_group.add_argument(
        "--reject-with-feedback",
        default=None,
        metavar="FEEDBACK",
        help="Reject the draft with this feedback at the interrupt",
    )
    parser.add_argument(
        "--max-document-characters",
        type=int,
        default=30_000,
        help="Maximum PDF text characters sent to the model",
    )
    parser.add_argument(
        "--max-reference-characters",
        type=int,
        default=4_000,
        help="Maximum characters kept for each retrieved reference",
    )
    parser.add_argument(
        "--max-reference-total-characters",
        type=int,
        default=12_000,
        help="Maximum total characters for the retrieved reference section",
    )
    parser.add_argument(
        "--max-revisions",
        type=int,
        default=2,
        help="Maximum approval rejection rounds before the workflow ends",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=100,
        help="Maximum PDF pages accepted by the extractor",
    )
    parser.add_argument(
        "--max-text-characters",
        type=int,
        default=1_000_000,
        help="Maximum extracted PDF text characters",
    )
    return parser


def _resolve_decision(args: argparse.Namespace) -> dict[str, object] | None:
    if args.approve:
        return {"decision": "approved", "feedback": ""}
    if args.reject_with_feedback is not None:
        return {"decision": "rejected", "feedback": args.reject_with_feedback}
    raw = input(
        "Workflow paused for approval. Enter a JSON decision "
        "(empty line pauses and keeps the checkpoint): "
    )
    raw = raw.strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON decision: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SystemExit("Decision must be a JSON object")
    decision = parsed.get("decision")
    if decision not in {"approved", "rejected"}:
        raise SystemExit(
            'Decision must be {"decision": "approved"} or '
            '{"decision": "rejected", "feedback": "..."}'
        )
    feedback = parsed.get("feedback", "")
    if not isinstance(feedback, str):
        raise SystemExit("Decision feedback must be a string")
    return {"decision": decision, "feedback": feedback}


async def run_workflow(args: argparse.Namespace) -> int:
    if not args.pdf.is_file():
        logger.error("PDF file not found: %s", args.pdf)
        return 1
    if not args.owner_key_hash:
        logger.error("--owner-key-hash is required for RAG retrieval")
        return 1

    rag_service = provide_rag_service()
    if rag_service is None:
        logger.error(
            "RAG is not enabled; set RAG_ENABLED=true and configure "
            "PostgreSQL/pgvector plus Ollama embedding"
        )
        return 1

    workflow = PDFReportWorkflow(
        extractor=PdfFileExtractor(
            max_pages=args.max_pages,
            max_text_characters=args.max_text_characters,
        ),
        retriever=RagServiceReportRetriever(rag_service),
        model=ProviderRouterReportModel(provide_llm_provider()),
        max_document_characters=args.max_document_characters,
        max_reference_characters=args.max_reference_characters,
        max_reference_total_characters=args.max_reference_total_characters,
    )
    graph = workflow.build()
    thread_id = args.thread_id or uuid.uuid4().hex
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    initial: PDFReportState = {
        "pdf_path": str(args.pdf),
        "report_topic": args.topic,
        "owner_key_hash": args.owner_key_hash,
        "model": args.model,
        "output_path": str(args.output) if args.output else None,
        "require_approval": not args.no_approval,
        "max_revisions": args.max_revisions,
    }

    raw_result = cast(dict[str, object], await graph.ainvoke(initial, config))
    paused = False
    while "__interrupt__" in raw_result:
        paused = True
        decision = _resolve_decision(args)
        if decision is None:
            break
        paused = False
        raw_result = cast(
            dict[str, object],
            await graph.ainvoke(Command(resume=decision), config),
        )

    summary = build_run_summary(
        cast(PDFReportState, raw_result),
        thread_id=thread_id,
        paused=paused,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    status = summary["status"]
    if status in {"completed", "pending_approval"}:
        return 0
    return 2


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = _build_parser()
    args = parser.parse_args()
    sys.exit(asyncio.run(run_workflow(args)))


if __name__ == "__main__":
    main()
