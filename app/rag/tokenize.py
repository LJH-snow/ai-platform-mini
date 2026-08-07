"""Chinese-aware keyword tokenization for tsvector search.

jieba is the primary tokenizer; a pure-stdlib fallback (regex-based,
CJK unigram) keeps ingestion and query paths working when the optional
dependency is unavailable.  Both implementations are reached through
one entry point so ingestion and query tokenization can never drift.

Error codes and serial numbers (``E10023``, ``E10024``) must remain
single tokens — jieba would split them — so they are masked with a
placeholder before tokenization and restored afterwards.  The masking
and restoration run inside ``tokenize_keywords``, so callers always see
the original text fragments in the returned token list.
"""

from __future__ import annotations

import re
from typing import Final

try:
    import jieba as _jieba  # type: ignore[import-untyped]

    _JIEBA_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised by dependency-free installs
    _jieba = None  # type: ignore[assignment]
    _JIEBA_AVAILABLE = False

# Protected fragments: 1-4 ASCII letters followed by 2+ digits
# (e.g. E10023, E10024).  Kept as single tokens for exact matching.
_PROTECTED_FRAGMENT_RE: Final = re.compile(r"[A-Z]{1,4}\d{2,}")

# Placeholders must survive tokenization intact on both paths: the
# stdlib path keeps any [A-Za-z0-9_]+ run, jieba treats a plain word
# like "ptk0" as one token.
_PLACEHOLDER_PREFIX: Final = "ptk"

# Stdlib fallback: ASCII word runs + single CJK ideographs.
_STDLIB_TOKEN_RE: Final = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")


def jieba_available() -> bool:
    """Whether the jieba backend is importable in this environment."""
    return _JIEBA_AVAILABLE


def tokenize_keywords(text: str) -> list[str]:
    """Tokenize ``text`` for tsvector search (Chinese-aware, code-safe).

    Returns the original text fragments (protected codes included) in
    token order; callers may join them with spaces for
    ``to_tsvector('simple', ...)`` / ``plainto_tsquery('simple', ...)``.
    """
    protected: dict[str, str] = {}

    def _protect(match: re.Match[str]) -> str:
        placeholder = f"{_PLACEHOLDER_PREFIX}{len(protected)}"
        protected[placeholder] = match.group(0)
        return placeholder

    masked = _PROTECTED_FRAGMENT_RE.sub(_protect, text)
    tokens = _tokenize_impl(masked)
    return [protected.get(token, token) for token in tokens]


def _tokenize_impl(text: str) -> list[str]:
    if _JIEBA_AVAILABLE:
        assert _jieba is not None
        return [token.strip() for token in _jieba.cut(text) if token.strip()]
    return _STDLIB_TOKEN_RE.findall(text)
