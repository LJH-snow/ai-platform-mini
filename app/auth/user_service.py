"""User service: registration, login, profile retrieval."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from app.auth.password import hash_password, verify_password
from app.auth.users_repository import UserRecord, UserRepository
from app.exceptions.base import AuthenticationError, ConflictError, ValidationError

logger = logging.getLogger(__name__)


class UserService:
    def __init__(self, repository: UserRepository) -> None:
        self._repository = repository

    async def register(
        self, email: str, display_name: str, password: str
    ) -> UserRecord:
        """Register a new user.  email is lower-cased internally."""
        email = email.strip().lower()
        if not email or "@" not in email:
            raise ValidationError("Invalid email address.")
        if not display_name or not display_name.strip():
            raise ValidationError("display_name is required.")
        if not password or len(password) < 6:
            raise ValidationError("Password must be at least 6 characters.")

        existing = await self._repository.find_by_email(email)
        if existing is not None:
            raise ConflictError(f"User with email '{email}' already exists.")

        salt, pw_hash = hash_password(password)
        record = UserRecord(
            id=str(uuid.uuid4()),
            email=email,
            display_name=display_name.strip(),
            password_salt=salt,
            password_hash=pw_hash,
            status="active",
            created_at=datetime.now(UTC),
        )
        saved = await self._repository.create(record)
        logger.info("user_registered email=%s user_id=%s", email, saved.id)
        return saved

    async def login(self, email: str, password: str) -> UserRecord:
        """Authenticate a user by email and password."""
        email = email.strip().lower()
        record = await self._repository.find_by_email(email)
        if record is None:
            raise AuthenticationError("Invalid email or password.")
        if record.status != "active":
            raise AuthenticationError("Account is disabled.")
        if not verify_password(password, record.password_salt, record.password_hash):
            raise AuthenticationError("Invalid email or password.")
        return record

    async def get_user(self, user_id: str) -> UserRecord | None:
        return await self._repository.find_by_id(user_id)
