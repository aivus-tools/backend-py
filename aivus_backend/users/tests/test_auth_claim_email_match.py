"""Auth-flow pending-brief claim must verify the brief contact email (MF-2).

The auth views (register/login/confirm-email/set-pending-brief) all route the
claim through ``_try_claim_pending_brief``. A brief carrying a contact email may
only be claimed by the matching account; a mismatch is skipped silently so the
auth response stays successful but the brief is never attached to the wrong user.
"""

from __future__ import annotations

import json

import pytest
from django.test import Client as DjangoTestClient
from django.urls import reverse

from aivus_backend.projects.models import Brief
from aivus_backend.users.api.auth_views import _try_claim_pending_brief
from aivus_backend.users.models import Client
from aivus_backend.users.models import User
from aivus_backend.users.tokens import AuthToken
from aivus_backend.users.tokens import TokenType


@pytest.fixture
def api_client() -> DjangoTestClient:
    return DjangoTestClient()


def _make_pending_user(email: str) -> User:
    return User.objects.create_user(
        email=email,
        password="p@ssw0rd1",
        name="Visitor",
        group="UNCONFIRMED",
    )


def _make_anon_brief(token: str, contact_email: str) -> Brief:
    return Brief.objects.create(
        client=None,
        anonymous_token=token,
        contact_email=contact_email,
    )


@pytest.mark.django_db
def test_claim_succeeds_when_email_matches():
    brief = _make_anon_brief("claim-tok-match", "match@example.com")
    user = _make_pending_user("match@example.com")
    user.pending_brief_id = brief.id
    user.pending_brief_token = "claim-tok-match"
    user.save(update_fields=["pending_brief_id", "pending_brief_token"])

    claimed = _try_claim_pending_brief(user)

    assert claimed == str(brief.id)
    brief.refresh_from_db()
    assert brief.client is not None
    assert brief.anonymous_token is None


@pytest.mark.django_db
def test_claim_is_case_insensitive_on_email():
    brief = _make_anon_brief("claim-tok-case", "Match@Example.com")
    user = _make_pending_user("match@example.com")
    user.pending_brief_id = brief.id
    user.pending_brief_token = "claim-tok-case"
    user.save(update_fields=["pending_brief_id", "pending_brief_token"])

    claimed = _try_claim_pending_brief(user)

    assert claimed == str(brief.id)


@pytest.mark.django_db
def test_claim_skipped_when_email_mismatch():
    brief = _make_anon_brief("claim-tok-bad", "owner@example.com")
    user = _make_pending_user("intruder@example.com")
    user.pending_brief_id = brief.id
    user.pending_brief_token = "claim-tok-bad"
    user.save(update_fields=["pending_brief_id", "pending_brief_token"])

    claimed = _try_claim_pending_brief(user)

    assert claimed is None
    brief.refresh_from_db()
    assert brief.client is None
    assert brief.anonymous_token == "claim-tok-bad"
    user.refresh_from_db()
    assert user.pending_brief_id is None
    assert user.pending_brief_token is None


@pytest.mark.django_db
def test_claim_allowed_when_brief_has_no_contact_email():
    brief = _make_anon_brief("claim-tok-empty", "")
    user = _make_pending_user("anyone@example.com")
    user.pending_brief_id = brief.id
    user.pending_brief_token = "claim-tok-empty"
    user.save(update_fields=["pending_brief_id", "pending_brief_token"])

    claimed = _try_claim_pending_brief(user)

    assert claimed == str(brief.id)


@pytest.mark.django_db
def test_confirm_email_flow_skips_mismatched_brief(api_client):
    brief = _make_anon_brief("confirm-tok-bad", "owner@example.com")
    user = _make_pending_user("intruder@example.com")
    user.pending_brief_id = brief.id
    user.pending_brief_token = "confirm-tok-bad"
    user.save(update_fields=["pending_brief_id", "pending_brief_token"])
    token_obj = AuthToken.create_token(user, TokenType.EMAIL_CONFIRMATION)

    response = api_client.get(
        reverse("auth_api:confirm-email"), {"token": token_obj.token}
    )

    assert response.status_code == 200
    body = response.json()
    assert "claimedBriefId" not in body
    brief.refresh_from_db()
    assert brief.client is None


@pytest.mark.django_db
def test_login_flow_skips_mismatched_brief(api_client):
    brief = _make_anon_brief("login-tok-bad", "owner@example.com")
    user = User.objects.create_user(
        email="client-intruder@example.com",
        password="p@ssw0rd1",
        name="Client",
        group="CLIENT",
    )
    Client.objects.create(owner=user, name="Co", ein="")

    response = api_client.post(
        reverse("auth_api:login"),
        data=json.dumps(
            {
                "email": "client-intruder@example.com",
                "password": "p@ssw0rd1",
                "briefId": str(brief.id),
                "briefToken": "login-tok-bad",
            }
        ),
        content_type="application/json",
    )

    assert response.status_code == 200
    body = response.json()
    assert "claimedBriefId" not in body
    brief.refresh_from_db()
    assert brief.client is None
    assert brief.anonymous_token == "login-tok-bad"
