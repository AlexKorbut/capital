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


def encrypt_bytes(data: bytes | bytearray) -> bytes | bytearray:
    """Encrypt a blob (a receipt photo). No-op if FERNET_KEY is unset (dev): the very buffer
    comes back, so the plain-storage path makes no extra copy of a 10 MB upload."""
    f = _fernet()
    return data if f is None else f.encrypt(bytes(data))  # Fernet takes ``bytes`` only


def decrypt_bytes(data: bytes) -> bytes:
    """Decrypt a blob. Tolerates plaintext (stored before encryption was enabled)."""
    f = _fernet()
    if f is None:
        return data
    try:
        return f.decrypt(data)
    except InvalidToken:
        # Same rule as ``decrypt``: a file written before the key was set reads back as-is.
        return data
