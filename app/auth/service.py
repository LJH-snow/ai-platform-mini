import logging
import secrets
from datetime import UTC, datetime

from app.auth.hash import hash_api_key
from app.auth.models import APIKey, APIKeyMetadata, APIKeyRecord
from app.auth.repository import APIKeyRepository
from app.exceptions.base import AuthenticationError, ConflictError, ValidationError

logger = logging.getLogger(__name__)

_THROTTLE_SECONDS = 60


class APIKeyService:
    def __init__(self, repository: APIKeyRepository) -> None:
        self._repository = repository
        self._touch_cache: dict[str, float] = {}

    async def validate(self, raw_key: str) -> APIKey:
        key_hash = hash_api_key(raw_key)
        record = await self._repository.find_by_key_hash(key_hash)
        if record is None:
            raise AuthenticationError("Invalid API key.")
        if record.status != "active":
            raise AuthenticationError("API key is disabled.")
        await self._throttled_touch(key_hash)
        return APIKey(key=key_hash, name=record.name)

    async def create_key(self, name: str) -> tuple[APIKeyMetadata, str]:
        raw_key = f"sk-{secrets.token_urlsafe(32)}"
        key_hash = hash_api_key(raw_key)
        now = datetime.now(UTC)
        record = APIKeyRecord(
            key_hash=key_hash,
            name=name,
            status="active",
            created_at=now,
        )
        saved = await self._repository.create_key(record)
        logger.info("api_key_created name=%s", name)
        metadata = APIKeyMetadata(
            key_hash_prefix=saved.key_hash[:8],
            name=saved.name,
            status=saved.status,
            created_at=saved.created_at,
            last_used_at=saved.last_used_at,
        )
        return metadata, raw_key

    async def revoke_key(self, key_hash: str) -> bool:
        revoked = await self._repository.update_status(key_hash, "revoked")
        if revoked:
            logger.info("api_key_revoked hash=%s", key_hash[:12])
        return revoked

    async def list_keys(self) -> list[APIKeyMetadata]:
        records = await self._repository.list_keys()
        return [
            APIKeyMetadata(
                key_hash_prefix=r.key_hash[:8],
                name=r.name,
                status=r.status,
                created_at=r.created_at,
                last_used_at=r.last_used_at,
            )
            for r in records
        ]

    async def find_hash_by_prefix(self, prefix: str) -> str | None:
        if len(prefix) != 8 or not all(c in "0123456789abcdef" for c in prefix):
            raise ValidationError(
                "key_hash_prefix must be exactly 8 lowercase hex characters."
            )
        matches = await self._repository.find_by_key_hash_prefix(prefix)
        if len(matches) == 0:
            return None
        if len(matches) > 1:
            raise ConflictError(f"key_hash_prefix '{prefix}' matches multiple keys.")
        return matches[0].key_hash

    async def ensure_initial_key(self, raw_key: str, name: str) -> bool:
        key_hash = hash_api_key(raw_key)
        record = APIKeyRecord(
            key_hash=key_hash,
            name=name,
            status="active",
            created_at=datetime.now(UTC),
        )
        created = await self._repository.ensure_key(record)
        if created:
            logger.info("initial_api_key_bootstrapped name=%s", name)
        else:
            logger.info("initial_api_key_already_exists name=%s", name)
        return created

    async def _throttled_touch(self, key_hash: str) -> None:
        import time

        now = time.monotonic()
        last = self._touch_cache.get(key_hash, 0)
        if now - last < _THROTTLE_SECONDS:
            return
        try:
            await self._repository.touch_last_used(key_hash)
        except Exception:
            self._touch_cache.pop(key_hash, None)
            logger.warning("touch_last_used failed for hash=%s", key_hash[:12])
            return
        self._touch_cache[key_hash] = now
