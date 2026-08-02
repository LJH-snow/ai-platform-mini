from typing import Annotated

from fastapi import APIRouter, Depends

from app.schemas.openai import OpenAIChatRequest, OpenAIChatResponse
from app.services.openai_service import OpenAIService, get_openai_service

router = APIRouter(tags=["openai"])


@router.post(
    "/v1/chat/completions",
    response_model=OpenAIChatResponse,
    summary="OpenAI-compatible chat completions endpoint",
)
async def create_chat_completions(
    request: OpenAIChatRequest,
    service: Annotated[OpenAIService, Depends(get_openai_service)],
) -> OpenAIChatResponse:
    return await service.chat_completions(request)
