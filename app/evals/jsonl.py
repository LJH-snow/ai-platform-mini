"""Safe, deterministic JSONL persistence for golden evaluation cases."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import TextIO

from app.evals.models import EvalCase


class GoldenDatasetError(ValueError):
    """Raised when a golden dataset violates its JSONL contract."""


def golden_case_to_json(case: EvalCase) -> str:
    """Serialize one case with stable key ordering and strict JSON."""

    return json.dumps(
        case.to_dict(),
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def golden_dataset_to_jsonl(cases: Iterable[EvalCase]) -> str:
    """Serialize cases in input order, ending with a newline for file parity."""

    normalized = validate_golden_dataset(tuple(cases))
    return "".join(f"{golden_case_to_json(case)}\n" for case in normalized)


def _reject_non_json_constant(value: str) -> None:
    """Reject JavaScript-style numeric constants accepted by Python's JSON parser."""

    raise ValueError(f"non-standard JSON constant: {value}")


def decode_jsonl_objects(
    source: str | Path | TextIO,
) -> tuple[tuple[int, dict[str, object]], ...]:
    """Decode JSONL text or a UTF-8 file into line-numbered object records."""

    if isinstance(source, Path):
        payload = source.read_text(encoding="utf-8")
    elif isinstance(source, str):
        payload = source
    else:
        payload = source.read()

    records: list[tuple[int, dict[str, object]]] = []
    for line_number, line in enumerate(payload.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            decoded = json.loads(line, parse_constant=_reject_non_json_constant)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            message = exc.msg if isinstance(exc, json.JSONDecodeError) else str(exc)
            raise GoldenDatasetError(
                f"invalid JSON on line {line_number}: {message}"
            ) from exc
        if not isinstance(decoded, dict):
            raise GoldenDatasetError(f"line {line_number} must contain a JSON object")
        records.append((line_number, decoded))
    return tuple(records)


def read_golden_dataset(source: str | Path | TextIO) -> tuple[EvalCase, ...]:
    """Read and validate JSONL text or a UTF-8 file without evaluating code."""

    cases: list[EvalCase] = []
    for line_number, decoded in decode_jsonl_objects(source):
        try:
            cases.append(EvalCase.from_dict(decoded))
        except (TypeError, ValueError) as exc:
            raise GoldenDatasetError(
                f"invalid case on line {line_number}: {exc}"
            ) from exc
    try:
        return validate_golden_dataset(cases)
    except GoldenDatasetError:
        raise


def write_golden_dataset(
    cases: Iterable[EvalCase],
    destination: Path | TextIO,
) -> None:
    """Write a validated dataset to a text stream or UTF-8 file."""

    payload = golden_dataset_to_jsonl(cases)
    if isinstance(destination, Path):
        destination.write_text(payload, encoding="utf-8")
    else:
        destination.write(payload)


def validate_golden_dataset(cases: Iterable[EvalCase]) -> tuple[EvalCase, ...]:
    """Validate case types and uniqueness while preserving dataset order."""

    normalized = tuple(cases)
    seen: set[str] = set()
    for index, case in enumerate(normalized):
        if not isinstance(case, EvalCase):
            raise GoldenDatasetError(f"dataset item {index} must be an EvalCase")
        if case.case_id in seen:
            raise GoldenDatasetError(f"duplicate case_id: {case.case_id}")
        seen.add(case.case_id)
    return normalized


load_golden_dataset = read_golden_dataset
save_golden_dataset = write_golden_dataset
