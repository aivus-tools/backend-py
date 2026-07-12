"""Tests for the mailbox connection API."""

import json
from unittest.mock import patch

import pytest
from django.conf import settings as django_settings
from django.test import Client as DjangoTestClient
from django.urls import reverse

from aivus_backend.email_agent.mailbox import MailboxAuthError
from aivus_backend.email_agent.models import EmailAccount
from aivus_backend.email_agent.models import EmailAccountStatus
from aivus_backend.users.models import User
from aivus_backend.users.models import Vendor

pytestmark = pytest.mark.django_db

CONNECT = "aivus_backend.email_agent.mailbox.test_connection"


@pytest.fixture
def api_client() -> DjangoTestClient:
    return DjangoTestClient()


@pytest.fixture
def vendor_user(db):
    user = User.objects.create_user(
        email="ea-vendor@example.com",
        password="p@ssw0rd",
        name="Vendor Owner",
        group="VENDOR",
    )
    vendor = Vendor.objects.create(name="Studio", owner=user)
    return user, vendor


def _auth(user) -> dict:
    return {
        "HTTP_X_API_KEY": django_settings.API_KEY,
        "HTTP_X_USER_ID": str(user.id),
        "HTTP_X_USER_GROUP": user.group,
    }


def test_connect_mailbox_success(api_client, vendor_user):
    user, vendor = vendor_user
    with patch(CONNECT):
        response = api_client.post(
            reverse("email_agent_api:connect-mailbox"),
            data=json.dumps(
                {"role": "agent", "email": "agent@vendor.com", "credential": "app-pw"}
            ),
            content_type="application/json",
            **_auth(user),
        )

    assert response.status_code == 201
    body = response.json()
    assert body["role"] == "agent"
    assert body["status"] == "connected"
    account = EmailAccount.objects.get(vendor=vendor, role="agent")
    assert account.credential == "app-pw"
    assert account.next_poll_at is not None


def test_connect_mailbox_bad_credentials_not_saved(api_client, vendor_user):
    user, vendor = vendor_user
    with patch(CONNECT, side_effect=MailboxAuthError("bad")):
        response = api_client.post(
            reverse("email_agent_api:connect-mailbox"),
            data=json.dumps(
                {"role": "agent", "email": "a@vendor.com", "credential": "x"}
            ),
            content_type="application/json",
            **_auth(user),
        )

    assert response.status_code == 400
    assert not EmailAccount.objects.filter(vendor=vendor).exists()


def test_connect_mailbox_validation(api_client, vendor_user):
    user, _vendor = vendor_user
    response = api_client.post(
        reverse("email_agent_api:connect-mailbox"),
        data=json.dumps({"role": "bogus"}),
        content_type="application/json",
        **_auth(user),
    )
    assert response.status_code == 400


def test_list_mailboxes(api_client, vendor_user):
    user, vendor = vendor_user
    EmailAccount.objects.create(vendor=vendor, role="monitor", email="info@vendor.com")
    response = api_client.get(reverse("email_agent_api:list-mailboxes"), **_auth(user))
    assert response.status_code == 200
    assert len(response.json()["mailboxes"]) == 1


def test_disconnect_wipes_credential(api_client, vendor_user):
    user, vendor = vendor_user
    account = EmailAccount.objects.create(
        vendor=vendor, role="agent", email="a@vendor.com", credential="secret"
    )
    response = api_client.post(
        reverse("email_agent_api:disconnect-mailbox", args=[account.id]),
        **_auth(user),
    )
    assert response.status_code == 200
    account.refresh_from_db()
    assert account.credential == ""
    assert account.status == EmailAccountStatus.DISCONNECTED
    assert account.deleted_at is not None
