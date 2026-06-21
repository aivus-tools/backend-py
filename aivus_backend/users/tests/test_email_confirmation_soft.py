"""Soft email confirmation: email_confirmed_at decouples confirmation from role.

Registration no longer parks a credential user in UNCONFIRMED waiting on email.
The user lands on their role (CLIENT when a brief is pending, otherwise CONFIRMED)
right away with email_confirmed_at=None, and confirming later just stamps the date.
A dashboard banner nags until then.
"""

from __future__ import annotations

import json

import pytest
from django.conf import settings
from django.test import Client as DjangoTestClient
from django.urls import reverse
from django.utils import timezone

from aivus_backend.projects.models import Brief
from aivus_backend.users.models import Client
from aivus_backend.users.models import User
from aivus_backend.users.tokens import AuthToken
from aivus_backend.users.tokens import TokenType


@pytest.fixture
def api_client() -> DjangoTestClient:
    return DjangoTestClient()


def _auth_headers(user, group=None):
    return {
        "HTTP_X_API_KEY": settings.API_KEY,
        "HTTP_X_USER_ID": str(user.id),
        "HTTP_X_USER_GROUP": group or user.group,
    }


def _register(api_client, **overrides):
    payload = {
        "email": "soft@example.com",
        "password": "Str0ngP@ss",
        "name": "Soft User",
        "authType": "CREDENTIALS",
    }
    payload.update(overrides)
    return api_client.post(
        reverse("auth_api:register"),
        data=json.dumps(payload),
        content_type="application/json",
    )


@pytest.mark.django_db
def test_register_credentials_without_brief_is_confirmed_but_unverified(api_client):
    response = _register(api_client)

    assert response.status_code == 201
    body = response.json()
    assert body["group"] == "CONFIRMED"
    assert body["emailConfirmedAt"] is None

    user = User.objects.get(email="soft@example.com")
    assert user.group == "CONFIRMED"
    assert user.email_confirmed_at is None


@pytest.mark.django_db
def test_register_credentials_with_pending_brief_claims_immediately(api_client):
    brief = Brief.objects.create(
        client=None,
        anonymous_token="soft-claim-tok",
        contact_email="soft@example.com",
    )

    response = _register(
        api_client,
        briefId=str(brief.id),
        briefToken="soft-claim-tok",
    )

    assert response.status_code == 201
    body = response.json()
    assert body["group"] == "CLIENT"
    assert body["claimedBriefId"] == str(brief.id)
    assert "clientId" in body
    assert body["emailConfirmedAt"] is None

    user = User.objects.get(email="soft@example.com")
    assert user.group == "CLIENT"
    assert user.email_confirmed_at is None
    brief.refresh_from_db()
    assert brief.client is not None
    assert brief.anonymous_token is None


@pytest.mark.django_db
def test_register_with_mismatched_brief_email_stays_roleless(api_client):
    brief = Brief.objects.create(
        client=None,
        anonymous_token="soft-mismatch-tok",
        contact_email="owner@example.com",
    )

    response = _register(
        api_client,
        briefId=str(brief.id),
        briefToken="soft-mismatch-tok",
    )

    assert response.status_code == 201
    body = response.json()
    assert body["group"] == "CONFIRMED"
    assert "claimedBriefId" not in body
    assert "clientId" not in body

    user = User.objects.get(email="soft@example.com")
    assert user.group == "CONFIRMED"
    # No orphan Client company is created on a mismatched claim.
    assert not Client.objects.filter(owner=user).exists()
    brief.refresh_from_db()
    assert brief.client is None
    assert brief.anonymous_token == "soft-mismatch-tok"


@pytest.mark.django_db
def test_register_google_marks_email_confirmed(api_client):
    response = api_client.post(
        reverse("auth_api:register"),
        data=json.dumps(
            {
                "email": "google-soft@example.com",
                "name": "Google Soft",
                "authType": "GOOGLE",
            }
        ),
        content_type="application/json",
        HTTP_X_INTERNAL_SECRET=settings.HMAC_SECRET,
    )

    assert response.status_code == 201
    assert response.json()["emailConfirmedAt"] is not None

    user = User.objects.get(email="google-soft@example.com")
    assert user.email_confirmed_at is not None


@pytest.mark.django_db
def test_confirm_email_stamps_date_and_is_idempotent(api_client):
    _register(api_client)
    user = User.objects.get(email="soft@example.com")
    token = AuthToken.objects.filter(
        user=user, token_type=TokenType.EMAIL_CONFIRMATION
    ).first()
    assert token is not None

    response = api_client.get(reverse("auth_api:confirm-email"), {"token": token.token})

    assert response.status_code == 200
    body = response.json()
    assert body["emailConfirmedAt"] is not None
    assert body["group"] == "CONFIRMED"

    user.refresh_from_db()
    assert user.email_confirmed_at is not None

    # Token is consumed; a second confirm with a fresh token is rejected.
    second_token = AuthToken.create_token(user, TokenType.EMAIL_CONFIRMATION)
    again = api_client.get(
        reverse("auth_api:confirm-email"), {"token": second_token.token}
    )
    assert again.status_code == 400


@pytest.mark.django_db
def test_login_returns_email_confirmed_at(api_client):
    _register(api_client)

    response = api_client.post(
        reverse("auth_api:login"),
        data=json.dumps({"email": "soft@example.com", "password": "Str0ngP@ss"}),
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["emailConfirmedAt"] is None


@pytest.mark.django_db
def test_resend_creates_token_only_while_unverified(api_client):
    _register(api_client)
    user = User.objects.get(email="soft@example.com")
    AuthToken.objects.filter(
        user=user, token_type=TokenType.EMAIL_CONFIRMATION
    ).delete()

    resend = lambda: api_client.post(  # noqa: E731
        reverse("auth_api:resend-confirmation"),
        data=json.dumps({"email": "soft@example.com"}),
        content_type="application/json",
    )

    assert resend().status_code == 200
    assert AuthToken.objects.filter(
        user=user, token_type=TokenType.EMAIL_CONFIRMATION
    ).exists()

    user.email_confirmed_at = timezone.now()
    user.save(update_fields=["email_confirmed_at"])
    AuthToken.objects.filter(
        user=user, token_type=TokenType.EMAIL_CONFIRMATION
    ).delete()

    assert resend().status_code == 200
    assert not AuthToken.objects.filter(
        user=user, token_type=TokenType.EMAIL_CONFIRMATION
    ).exists()


@pytest.mark.django_db
def test_user_me_exposes_email_confirmed_at(api_client):
    user = User.objects.create_user(
        email="me-soft@example.com",
        password="Str0ngP@ss",
        name="Me Soft",
        group="CLIENT",
    )

    response = api_client.get(reverse("user-me"), **_auth_headers(user))

    assert response.status_code == 200
    assert response.json()["emailConfirmedAt"] is None
