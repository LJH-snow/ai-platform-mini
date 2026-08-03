import logging
from datetime import UTC, datetime

from app.auth.hash import hash_api_key
from app.auth.models import APIKeyRecord

logger = logging.getLogger(__name__)


class InMemoryAPIKeyRepository:
    def __init__(self, api_keys: list[APIKeyRecord]) -> None:
        self._records: dict[str, APIKeyRecord] = {r.key_hash: r for r in api_keys}

    async def find_by_key_hash(self, key_hash: str) -> APIKeyRecord | None:
        return self._records.get(key_hash)

    async def find_by_key_hash_prefix(self, prefix: str) -> list[APIKeyRecord]:
        return [r for r in self._records.values() if r.key_hash.startswith(prefix)]

    async def list_keys(self) -> list[APIKeyRecord]:
        return list(self._records.values())

    async def create_key(self, record: APIKeyRecord) -> APIKeyRecord:
        self._records[record.key_hash] = record
        return record

    async def ensure_key(self, record: APIKeyRecord) -> bool:
        if record.key_hash in self._records:
            return False
        self._records[record.key_hash] = record
        return True

    async def update_status(self, key_hash: str, status: str) -> bool:
        record = self._records.get(key_hash)
        if record is None:
            return False
        record.status = status
        return True

    async def touch_last_used(self, key_hash: str) -> None:
        record = self._records.get(key_hash)
        if record is not None:
            record.last_used_at = datetime.now(UTC)


def create_in_memory_repository(raw_keys: str) -> InMemoryAPIKeyRepository:
    records: list[APIKeyRecord] = []
    if not raw_keys:
        logger.info("No API keys configured.")
        return InMemoryAPIKeyRepository(records)

    for entry in raw_keys.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":", 1)
        key = parts[0]
        name = parts[1] if len(parts) > 1 else key[:8]
        records.append(
            APIKeyRecord(
                key_hash=hash_api_key(key),
                name=name,
                status="active",
                created_at=datetime.now(UTC),
            )
        )

    logger.info("Loaded %d API key(s) into memory.", len(records))
    return InMemoryAPIKeyRepository(records)
