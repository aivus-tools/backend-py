"""Tests for the Fernet secret-encryption wrapper."""

from cryptography.fernet import Fernet
from django.test import override_settings

from aivus_backend.email_agent import crypto


def test_encrypt_decrypt_roundtrip():
    crypto.get_multifernet.cache_clear()
    token = crypto.encrypt("secret-value")
    assert token != "secret-value"
    assert crypto.decrypt(token) == "secret-value"


def test_rotation_reads_old_key_and_rewrites_to_primary():
    old = Fernet.generate_key().decode()
    new = Fernet.generate_key().decode()

    with override_settings(FERNET_KEYS=[old]):
        crypto.get_multifernet.cache_clear()
        token_v1 = crypto.encrypt("v1")

    with override_settings(FERNET_KEYS=[new, old]):
        crypto.get_multifernet.cache_clear()
        assert crypto.decrypt(token_v1) == "v1"
        token_v2 = crypto.encrypt("v2")

    with override_settings(FERNET_KEYS=[new]):
        crypto.get_multifernet.cache_clear()
        assert crypto.decrypt(token_v2) == "v2"

    crypto.get_multifernet.cache_clear()
