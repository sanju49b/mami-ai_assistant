"""Encrypt secrets before they go into the database (e.g. WHOOP tokens).

Fernet = symmetric encryption with a built-in integrity check: if the stored
value is tampered with, decryption fails instead of returning garbage.
"""
from cryptography.fernet import Fernet

from app.config import get_settings


class CryptoNotConfigured(RuntimeError):
    pass


def _fernet() -> Fernet:
    """Derive a Fernet key from APP_ENCRYPTION_KEY (any long random string).

    SHA-256 turns any secret into exactly 32 bytes; Fernet wants those bytes
    URL-safe base64-encoded. Changing APP_ENCRYPTION_KEY makes stored tokens
    unreadable, so keep it stable (you'd just reconnect WHOOP).
    """
    import base64
    import hashlib

    secret = get_settings().app_encryption_key
    if not secret:
        raise CryptoNotConfigured("APP_ENCRYPTION_KEY is not set")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode()
