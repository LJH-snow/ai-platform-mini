from pydantic import BaseModel


class ModelInfo(BaseModel):
    object: str = "model"
    id: str
    owned_by: str = "ollama"


class ModelsResponse(BaseModel):
    data: list[ModelInfo]
