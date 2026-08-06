"""Offline deterministic golden and RAG evaluation foundation."""

from app.evals.jsonl import (
    GoldenDatasetError,
    decode_jsonl_objects,
    golden_case_to_json,
    golden_dataset_to_jsonl,
    load_golden_dataset,
    read_golden_dataset,
    save_golden_dataset,
    validate_golden_dataset,
    write_golden_dataset,
)
from app.evals.matching import answer_matches_expected
from app.evals.models import (
    EvalCase,
    EvalCaseResult,
    EvalExecution,
    EvalSummary,
    EvaluationReport,
    EvaluationSummary,
    GoldenEvalCase,
    JSONValue,
)
from app.evals.rag_jsonl import (
    RAGDatasetError,
    load_rag_golden_dataset,
    rag_case_to_json,
    rag_dataset_to_jsonl,
    read_rag_golden_dataset,
    save_rag_golden_dataset,
    validate_rag_dataset,
    write_rag_golden_dataset,
)
from app.evals.rag_models import (
    RAGEvalCase,
    RAGEvalCaseResult,
    RAGExecution,
    RAGReport,
    RAGSummary,
    RetrievalOutcome,
    RetrievalReference,
    context_recall_at_k,
)
from app.evals.rag_runner import RAGEvaluationRunner, RunRAGCase
from app.evals.retrievers import (
    EmbeddingVectorStoreRetriever,
    RAGServiceRetriever,
)
from app.evals.runner import EvaluationRunner, RunCase
from app.evals.stats import average, percentile

__all__ = [
    "EmbeddingVectorStoreRetriever",
    "EvalCase",
    "EvalCaseResult",
    "EvalExecution",
    "EvalSummary",
    "EvaluationReport",
    "EvaluationRunner",
    "EvaluationSummary",
    "GoldenDatasetError",
    "GoldenEvalCase",
    "JSONValue",
    "RAGDatasetError",
    "RAGEvalCase",
    "RAGEvalCaseResult",
    "RAGEvaluationRunner",
    "RAGExecution",
    "RAGReport",
    "RAGServiceRetriever",
    "RAGSummary",
    "RetrievalOutcome",
    "RetrievalReference",
    "RunCase",
    "RunRAGCase",
    "answer_matches_expected",
    "average",
    "context_recall_at_k",
    "decode_jsonl_objects",
    "golden_case_to_json",
    "golden_dataset_to_jsonl",
    "load_golden_dataset",
    "load_rag_golden_dataset",
    "percentile",
    "rag_case_to_json",
    "rag_dataset_to_jsonl",
    "read_golden_dataset",
    "read_rag_golden_dataset",
    "save_golden_dataset",
    "save_rag_golden_dataset",
    "validate_golden_dataset",
    "validate_rag_dataset",
    "write_golden_dataset",
    "write_rag_golden_dataset",
]
