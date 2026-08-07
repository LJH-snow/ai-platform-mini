"""Password hashing with standard-library hashlib.scrypt (zero new dependencies)."""

import hashlib
import hmac
import os


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """Hash a password using scrypt.

    Returns (salt, hex_hash).  If salt is None a random 32-byte salt is generated.
    """
    if salt is None:
        salt = os.urandom(32).hex()
    key = hashlib.scrypt(
        password=password.encode("utf-8"),
        salt=salt.encode("utf-8"),
        n=16384,
        r=8,
        p=1,
        dklen=64,
    )
    return salt, key.hex()


def verify_password(password: str, salt: str, password_hash: str) -> bool:
    """Verify a password against a stored scrypt hash."""
    _, computed = hash_password(password, salt=salt)
    return hmac.compare_digest(computed, password_hash)
