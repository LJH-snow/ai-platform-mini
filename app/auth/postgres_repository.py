import logging
from datetime import UTC

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.models import APIKeyRecord
from app.db.models import APIKeyTable

logger = logging.getLogger(__name__)


class PostgresAPIKeyRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def find_by_key_hash(self, key_hash: str) -> APIKeyRecord | None:
        async with self._session_factory() as session:
            stmt = select(APIKeyTable).where(APIKeyTable.key_hash == key_hash)
            row = await session.scalar(stmt)
            if row is None:
                return None
            return _row_to_record(row)

    async def find_by_key_hash_prefix(self, prefix: str) -> list[APIKeyRecord]:
        async with self._session_factory() as session:
            stmt = select(APIKeyTable).where(APIKeyTable.key_hash.startswith(prefix))
            result = await session.scalars(stmt)
            return [_row_to_record(row) for row in result]

    async def list_keys(self) -> list[APIKeyRecord]:
        async with self._session_factory() as session:
            stmt = select(APIKeyTable).order_by(APIKeyTable.created_at)
            result = await session.scalars(stmt)
            return [_row_to_record(row) for row in result]

    async def create_key(self, record: APIKeyRecord) -> APIKeyRecord:
        async with self._session_factory() as session:
            row = APIKeyTable(
                key_hash=record.key_hash,
                name=record.name,
                status=record.status,
                user_id=record.user_id,
                workspace_id=record.workspace_id,
            )
            session.add(row)
            await session.commit()
            return _row_to_record(row)

    async def ensure_key(self, record: APIKeyRecord) -> bool:
        from sqlalchemy import text

        async with self._session_factory() as session:
            result = await session.execute(
                text(
                    "INSERT INTO api_keys (key_hash, name, status) "
                    "VALUES (:hash, :name, :status) "
                    "ON CONFLICT (key_hash) DO NOTHING"
                ),
                {"hash": record.key_hash, "name": record.name, "status": record.status},
            )
            await session.commit()
            return result.rowcount > 0  # type: ignore[attr-defined, no-any-return]

    async def update_status(self, key_hash: str, status: str) -> bool:
        async with self._session_factory() as session:
            existing = await session.scalar(
                select(APIKeyTable).where(APIKeyTable.key_hash == key_hash)
            )
            if existing is None:
                return False
            existing.status = status
            await session.commit()
            return True

    async def touch_last_used(self, key_hash: str) -> None:
        async with self._session_factory() as session:
            from datetime import datetime

            stmt = (
                update(APIKeyTable)
                .where(APIKeyTable.key_hash == key_hash)
                .values(last_used_at=datetime.now(UTC))
            )
            await session.execute(stmt)
            await session.commit()


def _row_to_record(row: APIKeyTable) -> APIKeyRecord:
    return APIKeyRecord(
        key_hash=row.key_hash,
        name=row.name,
        status=row.status,
        id=row.id,
        user_id=row.user_id,
        workspace_id=row.workspace_id,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
    )
