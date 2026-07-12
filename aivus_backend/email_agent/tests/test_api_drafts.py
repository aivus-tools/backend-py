"""Tests for the draft review API (S3-21)."""

import json
from unittest.mock import patch

import pytest
from django.conf import settings as django_settings
from django.test import Client as DjangoTestClient
from django.urls import reverse

from aivus_backend.email_agent.models import EmailAccount
from aivus_backend.email_agent.models import EmailAccountRole
from aivus_backend.email_agent.models import EmailAccountStatus
from aivus_backend.email_agent.models import EmailThread
from aivus_backend.email_agent.models import OutboundDraft
from aivus_backend.email_agent.models import OutboundDraftKind
from aivus_backend.email_agent.models import OutboundDraftStatus
from aivus_backend.users.models import User
from aivus_backend.users.models import Vendor

pytestmark = pytest.mark.django_db


class _SentMessage:
    provider_message_id = "<sent1@vendor.com>"


@pytest.fixture
def api_client() -> DjangoTestClient:
    return DjangoTestClient()


@pytest.fixture
def vendor_user(db):
    user = User.objects.create_user(
        email="ea-drafts@example.com",
        password="p@ssw0rd",
        name="Vendor Owner",
        group="VENDOR",
    )
    vendor = Vendor.objects.create(name="Studio", owner=user)
    EmailAccount.objects.create(
        vendor=vendor,
        role=EmailAccountRole.AGENT,
        email="agent@vendor.com",
        status=EmailAccountStatus.CONNECTED,
    )
    return user, vendor


def _auth(user) -> dict:
    return {
        "HTTP_X_API_KEY": django_settings.API_KEY,
        "HTTP_X_USER_ID": str(user.id),
        "HTTP_X_USER_GROUP": user.group,
    }


def _draft(vendor, **over):
    thread = EmailThread.objects.create(
        vendor=vendor,
        provider_thread_id="t1",
        client_email="jane@client.com",
        canonical_subject="New project",
        participants=["jane@client.com"],
    )
    defaults = {
        "thread": thread,
        "kind": OutboundDraftKind.FIRST_REPLY,
        "body": "Draft reply",
        "status": OutboundDraftStatus.PENDING,
        "metadata": {"variant": "A"},
    }
    defaults.update(over)
    return OutboundDraft.objects.create(**defaults)


def test_list_drafts_returns_pending(api_client, vendor_user):
    user, vendor = vendor_user
    _draft(vendor)

    response = api_client.get(reverse("email_agent_api:list-drafts"), **_auth(user))

    assert response.status_code == 200
    body = response.json()
    assert len(body["drafts"]) == 1
    assert body["drafts"][0]["variant"] == "A"


def test_approve_draft_sends(api_client, vendor_user):
    user, vendor = vendor_user
    draft = _draft(vendor)

    with patch(
        "aivus_backend.email_agent.drafts.sender.send_reply",
        return_value=_SentMessage(),
    ):
        response = api_client.post(
            reverse("email_agent_api:approve-draft", args=[draft.id]),
            **_auth(user),
        )

    assert response.status_code == 200
    assert response.json()["messageId"] == "<sent1@vendor.com>"
    draft.refresh_from_db()
    assert draft.status == OutboundDraftStatus.SENT


def test_approve_with_edit_body(api_client, vendor_user):
    user, vendor = vendor_user
    draft = _draft(vendor)

    with patch(
        "aivus_backend.email_agent.drafts.sender.send_reply",
        return_value=_SentMessage(),
    ) as send:
        api_client.post(
            reverse("email_agent_api:approve-draft", args=[draft.id]),
            data=json.dumps({"body": "Edited"}),
            content_type="application/json",
            **_auth(user),
        )

    assert send.call_args.args[2] == "Edited"


def test_edit_draft(api_client, vendor_user):
    user, vendor = vendor_user
    draft = _draft(vendor)

    response = api_client.post(
        reverse("email_agent_api:edit-draft", args=[draft.id]),
        data=json.dumps({"body": "New text"}),
        content_type="application/json",
        **_auth(user),
    )

    assert response.status_code == 200
    draft.refresh_from_db()
    assert draft.body == "New text"


def test_reject_draft(api_client, vendor_user):
    user, vendor = vendor_user
    draft = _draft(vendor)

    response = api_client.post(
        reverse("email_agent_api:reject-draft", args=[draft.id]),
        **_auth(user),
    )

    assert response.status_code == 200
    draft.refresh_from_db()
    assert draft.status == OutboundDraftStatus.REJECTED


def test_approve_already_sent_conflict(api_client, vendor_user):
    user, vendor = vendor_user
    draft = _draft(vendor, status=OutboundDraftStatus.SENT)

    response = api_client.post(
        reverse("email_agent_api:approve-draft", args=[draft.id]),
        **_auth(user),
    )

    assert response.status_code == 409


def test_other_vendor_draft_is_not_found(api_client, vendor_user):
    user, _vendor = vendor_user
    other_owner = User.objects.create_user(
        email="other@example.com", password="p@ssw0rd", name="Other", group="VENDOR"
    )
    other_vendor = Vendor.objects.create(name="Other Studio", owner=other_owner)
    draft = _draft(other_vendor)

    response = api_client.post(
        reverse("email_agent_api:reject-draft", args=[draft.id]),
        **_auth(user),
    )

    assert response.status_code == 404
