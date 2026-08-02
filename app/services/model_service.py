import logging
from typing import Annotated

from fastapi import Depends

from app.core.container import provide_llm_provider
from app.exceptions.ollama import OllamaServiceError
from app.providers.base import LLMProvider
from app.providers.results import ProviderModelEntry
from app.schemas.models import ModelInfo, ModelsResponse

logger = logging.getLogger(__name__)


class ModelService:
    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    async def list_models(self) -> ModelsResponse:
        data = await self._provider.list_models()
        model_entries = self._parse_models_response(data)
        return ModelsResponse(
            data=[ModelInfo(id=entry.name) for entry in model_entries]
        )

    def _parse_models_response(
        self, data: dict[str, object]
    ) -> list[ProviderModelEntry]:
        raw_models = data.get("models")
        if not isinstance(raw_models, list):
            raise OllamaServiceError(
                "Ollama response did not include a valid models list."
            )

        entries: list[ProviderModelEntry] = []
        for item in raw_models:
            if not isinstance(item, dict):
                logger.warning("Skipping non-dict model entry in Ollama response.")
                continue
            name = item.get("name")
            if isinstance(name, str):
                entries.append(ProviderModelEntry(name=name))
        return entries


def get_model_service(
    provider: Annotated[LLMProvider, Depends(provide_llm_provider)],
) -> ModelService:
    return ModelService(provider)
