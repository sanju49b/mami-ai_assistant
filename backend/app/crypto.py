"""Encrypt secrets before they go into the database (e.g. WHOOP tokens).

Fernet = symmetric encryption with a built-in integrity check: if the stored
value is tampered with, decryption fails instead of returning garbage.
"""
from cryptography.fernet import Fernet

from app.config import get_settings


class CryptoNotConfigured(RuntimeError):
    pass


def _fernet() -> Fernet:
    key = get_settings().app_encryption_key
    if not key:
        raise CryptoNotConfigured("APP_ENCRYPTION_KEY is not set")
    return Fernet(key.encode())


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode()
