"""Symmetric encryption for secrets at rest (Fernet + key rotation).

Refresh tokens and other per-vendor secrets are stored encrypted. The key
material lives only in the ``FERNET_KEYS`` env var, newest first: the first key
encrypts new writes, every key can decrypt, so rotation is prepend-a-new-key.
Rotating the ciphertext onto the new primary is done by ``reencrypt_secrets``.
"""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet
from cryptography.fernet import InvalidToken
from cryptography.fernet import MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models


@lru_cache(maxsize=1)
def get_multifernet() -> MultiFernet:
    """Build a MultiFernet from ``settings.FERNET_KEYS`` (newest key first)."""
    keys = getattr(settings, "FERNET_KEYS", None) or []
    if not keys:
        message = "FERNET_KEYS is not configured; cannot encrypt/decrypt secrets."
        raise ImproperlyConfigured(message)
    fernets = [Fernet(key if isinstance(key, bytes) else key.encode()) for key in keys]
    return MultiFernet(fernets)


def encrypt(plaintext: str) -> str:
    """Encrypt a string with the primary key, return a url-safe token."""
    return get_multifernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """Decrypt a token produced by :func:`encrypt` using any known key."""
    return get_multifernet().decrypt(token.encode()).decode()


class EncryptedTextField(models.TextField):
    """A ``TextField`` transparently encrypted at rest.

    The stored column holds Fernet ciphertext; the Python value is plaintext.
    Encrypted columns cannot be filtered, indexed, or ordered at the DB level.
    """

    def from_db_value(
        self,
        value: str | None,
        expression: object,
        connection: object,
    ) -> str | None:
        if value is None or value == "":
            return value
        try:
            return decrypt(value)
        except InvalidToken:
            return value

    def get_prep_value(self, value: str | None) -> str | None:
        if value is None or value == "":
            return value
        return encrypt(str(value))
