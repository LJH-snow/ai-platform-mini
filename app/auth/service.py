import logging

from app.auth.models import APIKey
from app.core.settings import get_settings

logger = logging.getLogger(__name__)


class APIKeyService:
    def __init__(self, api_keys: list[APIKey]) -> None:
        self._key_set: set[str] = {k.key for k in api_keys}
        self._key_map: dict[str, APIKey] = {k.key: k for k in api_keys}

    def validate(self, raw_key: str) -> APIKey:
        if raw_key in self._key_set:
            return self._key_map[raw_key]
        raise ValueError("Invalid API key")

    @property
    def key_count(self) -> int:
        return len(self._key_set)


def create_api_key_service() -> APIKeyService:
    settings = get_settings()
    raw_keys = settings.api_keys
    if not raw_keys:
        logger.info("No API keys configured. Authentication is effectively disabled.")
        return APIKeyService(api_keys=[])

    keys: list[APIKey] = []
    for entry in raw_keys.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":", 1)
        key = parts[0]
        name = parts[1] if len(parts) > 1 else key[:8]
        keys.append(APIKey(key=key, name=name))

    logger.info("Loaded %d API key(s).", len(keys))
    return APIKeyService(api_keys=keys)
