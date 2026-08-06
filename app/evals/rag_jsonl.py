"""Safe, deterministic JSONL persistence for RAG golden evaluation cases."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import TextIO

from app.evals.jsonl import GoldenDatasetError, decode_jsonl_objects
from app.evals.rag_models import RAGEvalCase


class RAGDatasetError(GoldenDatasetError):
    """Raised when a RAG golden dataset violates its JSONL contract."""


def rag_case_to_json(case: RAGEvalCase) -> str:
    """Serialize one RAG case with stable key ordering and strict JSON."""

    return json.dumps(
        case.to_dict(),
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def rag_dataset_to_jsonl(cases: Iterable[RAGEvalCase]) -> str:
    """Serialize RAG cases in input order, ending with a newline."""

    normalized = validate_rag_dataset(tuple(cases))
    return "".join(f"{rag_case_to_json(case)}\n" for case in normalized)


def read_rag_golden_dataset(source: str | Path | TextIO) -> tuple[RAGEvalCase, ...]:
    """Read and validate RAG JSONL text or a UTF-8 file without executing code."""

    cases: list[RAGEvalCase] = []
    for line_number, decoded in decode_jsonl_objects(source):
        try:
            cases.append(RAGEvalCase.from_dict(decoded))
        except (TypeError, ValueError) as exc:
            raise RAGDatasetError(
                f"invalid RAG case on line {line_number}: {exc}"
            ) from exc
    try:
        return validate_rag_dataset(cases)
    except RAGDatasetError:
        raise


def write_rag_golden_dataset(
    cases: Iterable[RAGEvalCase],
    destination: Path | TextIO,
) -> None:
    """Write a validated RAG dataset to a text stream or UTF-8 file."""

    payload = rag_dataset_to_jsonl(cases)
    if isinstance(destination, Path):
        destination.write_text(payload, encoding="utf-8")
    else:
        destination.write(payload)


def validate_rag_dataset(cases: Iterable[RAGEvalCase]) -> tuple[RAGEvalCase, ...]:
    """Validate case types and uniqueness while preserving dataset order."""

    normalized = tuple(cases)
    seen: set[str] = set()
    for index, case in enumerate(normalized):
        if not isinstance(case, RAGEvalCase):
            raise RAGDatasetError(f"dataset item {index} must be a RAGEvalCase")
        if case.case_id in seen:
            raise RAGDatasetError(f"duplicate case_id: {case.case_id}")
        seen.add(case.case_id)
    return normalized


load_rag_golden_dataset = read_rag_golden_dataset
save_rag_golden_dataset = write_rag_golden_dataset
