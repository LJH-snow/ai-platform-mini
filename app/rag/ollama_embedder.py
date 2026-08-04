import logging
import math
from typing import Any

import httpx

from app.exceptions.base import ProviderError, ProviderUnavailableError

logger = logging.getLogger(__name__)


class OllamaEmbedder:
    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "nomic-embed-text",
        dimensions: int = 768,
        timeout_seconds: float = 60.0,
    ) -> None:
        self._model = model
        self._dimensions = dimensions
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds),
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload: dict[str, object] = {
            "model": self._model,
            "input": texts,
        }
        response_data = await self._request(payload)
        embeddings = self._extract_embeddings(response_data, len(texts))
        self._validate_embeddings(embeddings)
        return embeddings

    async def embed_query(self, text: str) -> list[float]:
        results = await self.embed([text])
        if not results:
            raise ProviderError("Embedding service returned no result for query")
        return results[0]

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(self, payload: dict[str, object]) -> dict[str, object]:
        try:
            response = await self._client.post("/api/embed", json=payload)
            response.raise_for_status()
        except httpx.ConnectError as exc:
            raise ProviderUnavailableError(
                f"Embedding service unavailable: {exc}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                f"Embedding service returned {exc.response.status_code}"
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderUnavailableError(
                f"Embedding service timed out: {exc}"
            ) from exc
        except httpx.RequestError as exc:
            # Catch remaining network-level errors (ReadError,
            # WriteError, RemoteProtocolError, etc.) that are not
            # ConnectError or TimeoutException but still indicate
            # the embedding service is unreachable.
            raise ProviderUnavailableError(
                f"Embedding service request failed: {exc}"
            ) from exc
        try:
            data: Any = response.json()
        except Exception as exc:
            raise ProviderError(f"Invalid embedding response: {exc}") from exc
        if not isinstance(data, dict):
            raise ProviderError("Embedding response must be a JSON object")
        return data

    def _extract_embeddings(
        self, data: dict[str, object], expected_count: int
    ) -> list[list[float]]:
        embeddings = data.get("embeddings")
        if not isinstance(embeddings, list):
            raise ProviderError("Embedding response missing 'embeddings' array")
        if len(embeddings) != expected_count:
            raise ProviderError(
                f"Embedding count mismatch: expected {expected_count}, "
                f"got {len(embeddings)}"
            )
        return embeddings

    def _validate_embeddings(self, embeddings: list[list[float]]) -> None:
        for i, vec in enumerate(embeddings):
            if not isinstance(vec, list):
                raise ProviderError(f"Embedding {i} is not a list")
            if len(vec) != self._dimensions:
                raise ProviderError(
                    f"Embedding {i} dimension mismatch: expected "
                    f"{self._dimensions}, got {len(vec)}"
                )
            if not all(
                isinstance(v, (int, float))
                and not isinstance(v, bool)
                and math.isfinite(v)
                for v in vec
            ):
                raise ProviderError(f"Embedding {i} contains invalid numeric values")
