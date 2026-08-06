"""Shared deterministic answer matching helpers."""

from __future__ import annotations

from collections.abc import Sequence


def answer_matches_expected(
    expected_fragments: Sequence[str] | None,
    answer: str | None,
) -> bool:
    """Return whether every declared fragment appears in the answer."""

    if expected_fragments is None:
        return True
    if answer is None:
        return False
    return all(fragment in answer for fragment in expected_fragments)
