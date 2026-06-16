"""Field-level encryption at rest (NFR-001) using Fernet (AES-128-CBC + HMAC).

Used to encrypt sensitive asset fields (wallet_address, notes, raw_input).
Generate a key: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from core.config import settings


@lru_cache
def _fernet() -> Fernet | None:
    if not settings.fernet_key:
        return None
    return Fernet(settings.fernet_key.encode())


def encrypt(plaintext: str | None) -> str | None:
    """Encrypt a string. Returns None for None. No-op if FERNET_KEY unset (dev)."""
    if plaintext is None:
        return None
    f = _fernet()
    if f is None:
        return plaintext
    return f.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str | None) -> str | None:
    """Decrypt a string. Tolerates plaintext (pre-encryption / key-unset) values."""
    if ciphertext is None:
        return None
    f = _fernet()
    if f is None:
        return ciphertext
    try:
        return f.decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        # Value was stored before encryption was enabled — return as-is.
        return ciphertext
