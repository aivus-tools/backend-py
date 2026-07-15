"""Tests for the email-agent deploy checks."""

import pytest
from cryptography.fernet import Fernet
from django.test import override_settings

from aivus_backend.email_agent import checks
from aivus_backend.email_agent import crypto
from aivus_backend.email_agent.models import EmailAccount
from aivus_backend.email_agent.models import EmailAccountRole

pytestmark = pytest.mark.django_db


def _ids(errors):
    return {error.id for error in errors}


def test_no_errors_when_keys_configured_and_no_accounts():
    key = Fernet.generate_key().decode()
    with override_settings(FERNET_KEYS=[key]):
        errors = checks.fernet_keys_configured(app_configs=None)
    assert errors == []


def test_flags_missing_keys():
    with override_settings(FERNET_KEYS=[]):
        errors = checks.fernet_keys_configured(app_configs=None)
    assert "email_agent.E001" in _ids(errors)


def test_flags_malformed_keys():
    with override_settings(FERNET_KEYS=["not-a-fernet-key"]):
        crypto.get_multifernet.cache_clear()
        errors = checks.fernet_keys_configured(app_configs=None)
    assert "email_agent.E002" in _ids(errors)


def test_flags_undecryptable_credential_after_bad_rotation(vendor):
    key_old = Fernet.generate_key().decode()
    key_new = Fernet.generate_key().decode()

    with override_settings(FERNET_KEYS=[key_old]):
        crypto.get_multifernet.cache_clear()
        EmailAccount.objects.create(
            vendor=vendor,
            role=EmailAccountRole.AGENT,
            email="agent@vendor.com",
            credential="app-password-value",
        )

    with override_settings(FERNET_KEYS=[key_new]):
        crypto.get_multifernet.cache_clear()
        errors = checks.fernet_keys_configured(app_configs=None)

    assert "email_agent.E003" in _ids(errors)
