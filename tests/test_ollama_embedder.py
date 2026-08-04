from unittest.mock import patch

import httpx
import pytest

from app.exceptions.base import ProviderError, ProviderUnavailableError
from app.rag.ollama_embedder import OllamaEmbedder


@pytest.fixture
def embedder() -> OllamaEmbedder:
    return OllamaEmbedder(
        base_url="http://localhost:11434",
        model="nomic-embed-text",
        dimensions=4,
        timeout_seconds=5.0,
    )


def _make_response_data(embeddings: list[list[float]]) -> dict[str, object]:
    return {"model": "nomic-embed-text", "embeddings": embeddings}


class TestOllamaEmbedderExtract:
    """Tests for _extract_embeddings and _validate_embeddings (no HTTP)."""

    def test_extract_valid_embeddings(self, embedder: OllamaEmbedder) -> None:
        data = _make_response_data([[0.1, 0.2, 0.3, 0.4], [0.5, 0.6, 0.7, 0.8]])
        result = embedder._extract_embeddings(data, 2)
        assert len(result) == 2

    def test_extract_missing_embeddings_key(self, embedder: OllamaEmbedder) -> None:
        data: dict[str, object] = {"model": "test"}
        with pytest.raises(ProviderError, match="missing 'embeddings' array"):
            embedder._extract_embeddings(data, 1)

    def test_extract_count_mismatch(self, embedder: OllamaEmbedder) -> None:
        data = _make_response_data([[0.1, 0.2, 0.3, 0.4]])
        with pytest.raises(ProviderError, match="count mismatch"):
            embedder._extract_embeddings(data, 2)

    def test_validate_dimension_mismatch(self, embedder: OllamaEmbedder) -> None:
        embeddings = [[0.1, 0.2]]  # 2 dims instead of 4
        with pytest.raises(ProviderError, match="dimension mismatch"):
            embedder._validate_embeddings(embeddings)

    def test_validate_non_numeric_values(self, embedder: OllamaEmbedder) -> None:
        embeddings: list[list[float]] = [[0.1, 0.2, 0.3, "bad"]]  # type: ignore[list-item]
        with pytest.raises(ProviderError, match="invalid numeric values"):
            embedder._validate_embeddings(embeddings)  # type: ignore[arg-type]

    def test_validate_not_a_list(self, embedder: OllamaEmbedder) -> None:
        # Pass a list containing a non-list element to trigger validation
        bad_embeddings: list[object] = [42]
        with pytest.raises(ProviderError, match="not a list"):
            embedder._validate_embeddings(bad_embeddings)  # type: ignore[arg-type]

    def test_validate_correct_embeddings(self, embedder: OllamaEmbedder) -> None:
        embeddings = [[0.1, 0.2, 0.3, 0.4], [0.5, 0.6, 0.7, 0.8]]
        # Should not raise
        embedder._validate_embeddings(embeddings)


class TestOllamaEmbedderEmbedEmpty:
    @pytest.mark.asyncio
    async def test_embed_empty_list_returns_empty(
        self, embedder: OllamaEmbedder
    ) -> None:
        result = await embedder.embed([])
        assert result == []


class TestOllamaEmbedderNetworkErrors:
    """Test that all httpx network errors map to ProviderUnavailableError."""

    @pytest.mark.asyncio
    async def test_read_error_maps_to_provider_unavailable(self) -> None:
        """httpx.ReadError should map to ProviderUnavailableError (502)."""
        embedder = OllamaEmbedder(dimensions=4)

        with patch.object(
            embedder._client,
            "post",
            side_effect=httpx.ReadError(
                "connection reset",
                request=httpx.Request("POST", "http://localhost/api/embed"),
            ),
        ):
            with pytest.raises(
                ProviderUnavailableError, match="Embedding service request failed"
            ):
                await embedder.embed(["test"])

        await embedder.close()

    @pytest.mark.asyncio
    async def test_remote_protocol_error_maps_to_provider_unavailable(self) -> None:
        """httpx.RemoteProtocolError should map to ProviderUnavailableError."""
        embedder = OllamaEmbedder(dimensions=4)

        with patch.object(
            embedder._client,
            "post",
            side_effect=httpx.RemoteProtocolError(
                "protocol error",
                request=httpx.Request("POST", "http://localhost/api/embed"),
            ),
        ):
            with pytest.raises(
                ProviderUnavailableError, match="Embedding service request failed"
            ):
                await embedder.embed(["test"])

        await embedder.close()
