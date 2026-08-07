"""Deterministic mock embedder for golden-set comparison without Ollama.

Token vectors are drawn from a fixed-seed generator on first encounter,
so within one corpus ingestion the same token always maps to the same
unit vector (determinism is scoped to a fixed corpus/order, not across
processes).  Document vectors are TF-IDF-weighted sums of token vectors
(rare tokens dominate, giving distinct documents measurable similarity
gradients) normalized to unit length.  Query embedding uses the
vocabulary counts collected during ingestion, so tokens that appear in
many corpus chunks contribute less to similarity.

Designed for **relative** comparison (hybrid vs vector-only retrieval)
in CI, not for absolute semantic quality.
"""

from __future__ import annotations

import math
import random
from collections import Counter

from app.rag.tokenize import tokenize_keywords


class MockEmbedder:
    def __init__(self, dimensions: int = 768, seed: int = 42) -> None:
        self._dimensions = dimensions
        self._rng = random.Random(seed)
        self._token_vectors: dict[str, list[float]] = {}
        self._doc_freq: Counter[str] = Counter()
        self._docs_seen = 0

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a corpus batch, collecting document frequencies first."""
        batch_tokens = [set(tokenize_keywords(text)) for text in texts]
        for tokens in batch_tokens:
            self._docs_seen += 1
            for token in tokens:
                self._doc_freq[token] += 1
        return [self._embed_tokens(tokens) for tokens in batch_tokens]

    async def embed_query(self, text: str) -> list[float]:
        """Embed one query against the vocabulary learned from ingestion."""
        return self._embed_tokens(set(tokenize_keywords(text)))

    async def close(self) -> None:
        """Nothing to release; present for the Embedder protocol."""

    def _embed_tokens(self, tokens: set[str]) -> list[float]:
        vector = [0.0] * self._dimensions
        for token in tokens:
            idf = math.log((self._docs_seen + 1) / (self._doc_freq[token] + 1)) + 1.0
            token_vector = self._token_vector(token)
            for index, weight in enumerate(token_vector):
                vector[index] += idf * weight
        return _normalize(vector)

    def _token_vector(self, token: str) -> list[float]:
        """Deterministic unit vector for one token (fixed seed)."""
        vector = self._token_vectors.get(token)
        if vector is None:
            vector = [self._rng.uniform(-1.0, 1.0) for _ in range(self._dimensions)]
            vector = _normalize(vector)
            self._token_vectors[token] = vector
        return vector


def _normalize(vector: list[float]) -> list[float]:
    """Return the unit-length projection of a vector (zeros stay zeros)."""
    magnitude = math.sqrt(sum(value * value for value in vector))
    if magnitude == 0.0:
        return vector
    return [value / magnitude for value in vector]
