"""Small numerical helpers shared by evaluation runners."""

from __future__ import annotations

import math
from collections.abc import Sequence


def average(values: Sequence[float]) -> float | None:
    """Return the arithmetic mean, or None for an empty sequence."""

    return sum(values) / len(values) if values else None


def percentile(values: Sequence[float], *, percentile: float) -> float:
    """Compute an interpolated percentile; empty input returns ``0.0``."""

    if not values:
        return 0.0
    if not 0 <= percentile <= 1:
        raise ValueError("percentile must be between zero and one")
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction
