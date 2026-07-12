"""Tests for email_agent models: encryption at rest and uniqueness constraints."""

import pytest
from django.db import IntegrityError
from django.db import connection

from aivus_backend.email_agent import crypto
from aivus_backend.email_agent.models import AutonomyMode
from aivus_backend.email_agent.models import EmailAccount
from aivus_backend.email_agent.models import EmailAccountRole
from aivus_backend.email_agent.models import EmailDirection
from aivus_backend.email_agent.models import EmailMessage
from aivus_backend.email_agent.models import EmailThread
from aivus_backend.email_agent.models import VendorAgentProfile

pytestmark = pytest.mark.django_db


def test_credential_encrypted_at_rest(vendor):
    crypto.get_multifernet.cache_clear()
    account = EmailAccount.objects.create(
        vendor=vendor,
        role=EmailAccountRole.AGENT,
        email="agent@example.com",
        credential="app-password-abc",
    )

    account.refresh_from_db()
    assert account.credential == "app-password-abc"

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT credential FROM email_agent_emailaccount WHERE id = %s",
            [str(account.id)],
        )
        raw = cursor.fetchone()[0]

    assert raw != "app-password-abc"
    assert crypto.decrypt(raw) == "app-password-abc"


def test_autonomy_mode_defaults_to_draft(vendor):
    profile = VendorAgentProfile.objects.create(vendor=vendor)
    assert profile.autonomy_mode == AutonomyMode.DRAFT


def test_message_unique_per_account(vendor):
    account = EmailAccount.objects.create(
        vendor=vendor,
        role=EmailAccountRole.MONITOR,
        email="monitor@example.com",
    )
    thread = EmailThread.objects.create(vendor=vendor, provider_thread_id="t1")
    EmailMessage.objects.create(
        account=account,
        thread=thread,
        provider_message_id="m1",
        direction=EmailDirection.IN,
    )
    with pytest.raises(IntegrityError):
        EmailMessage.objects.create(
            account=account,
            thread=thread,
            provider_message_id="m1",
            direction=EmailDirection.IN,
        )


def test_thread_unique_per_vendor(vendor):
    EmailThread.objects.create(vendor=vendor, provider_thread_id="dup")
    with pytest.raises(IntegrityError):
        EmailThread.objects.create(vendor=vendor, provider_thread_id="dup")
