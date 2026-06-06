"""Tests for the hard-gated e2e_confirmation_token endpoint."""

from datetime import timedelta

import pytest
from django.test import Client as DjangoTestClient
from django.test import override_settings
from django.utils import timezone

from aivus_backend.users.models import User
from aivus_backend.users.tokens import AuthToken
from aivus_backend.users.tokens import TokenType

URL = "/api/v1/auth/e2e-confirmation-token"
SECRET = "staging-only-secret"


@pytest.fixture
def api_client():
    return DjangoTestClient()


@pytest.fixture
def user_with_token(db):
    user = User.objects.create_user(
        email="confirm-me@example.com",
        password="securepass123",
        name="Confirm Me",
        group="UNCONFIRMED",
        auth_type="CREDENTIALS",
    )
    token = AuthToken.create_token(user, TokenType.EMAIL_CONFIRMATION)
    return user, token


class TestE2EConfirmationToken:
    def test_disabled_by_default_returns_404(self, api_client, user_with_token):
        response = api_client.get(
            f"{URL}?email=confirm-me@example.com",
            headers={"X-E2E-Token-Secret": SECRET},
        )
        assert response.status_code == 404

    @override_settings(
        E2E_CONFIRMATION_TOKEN_ENABLED=True,
        E2E_CONFIRMATION_TOKEN_SECRET=SECRET,
    )
    def test_wrong_secret_returns_404(self, api_client, user_with_token):
        # 404 (not 403) so an enabled endpoint is indistinguishable from a
        # missing route — no signal that there is a secret to guess.
        response = api_client.get(
            f"{URL}?email=confirm-me@example.com",
            headers={"X-E2E-Token-Secret": "wrong"},
        )
        assert response.status_code == 404

    @override_settings(
        E2E_CONFIRMATION_TOKEN_ENABLED=True,
        E2E_CONFIRMATION_TOKEN_SECRET="",
    )
    def test_empty_configured_secret_returns_404(self, api_client, user_with_token):
        response = api_client.get(
            f"{URL}?email=confirm-me@example.com",
            headers={"X-E2E-Token-Secret": ""},
        )
        assert response.status_code == 404

    @override_settings(
        E2E_CONFIRMATION_TOKEN_ENABLED=True,
        E2E_CONFIRMATION_TOKEN_SECRET=SECRET,
    )
    def test_non_ascii_secret_header_returns_404(self, api_client, user_with_token):
        # bytes-compare must not raise on a non-ASCII header (no 500).
        response = api_client.get(
            f"{URL}?email=confirm-me@example.com",
            headers={"X-E2E-Token-Secret": "wrøng-ключ"},
        )
        assert response.status_code == 404

    @override_settings(
        E2E_CONFIRMATION_TOKEN_ENABLED=True,
        E2E_CONFIRMATION_TOKEN_SECRET=SECRET,
    )
    def test_missing_email_returns_400(self, api_client, user_with_token):
        response = api_client.get(URL, headers={"X-E2E-Token-Secret": SECRET})
        assert response.status_code == 400

    @override_settings(
        E2E_CONFIRMATION_TOKEN_ENABLED=True,
        E2E_CONFIRMATION_TOKEN_SECRET=SECRET,
    )
    def test_unknown_email_returns_404(self, api_client, user_with_token):
        response = api_client.get(
            f"{URL}?email=nobody@example.com",
            headers={"X-E2E-Token-Secret": SECRET},
        )
        assert response.status_code == 404

    @override_settings(
        E2E_CONFIRMATION_TOKEN_ENABLED=True,
        E2E_CONFIRMATION_TOKEN_SECRET=SECRET,
    )
    def test_returns_latest_token(self, api_client, user_with_token):
        user, _token = user_with_token
        newer = AuthToken.create_token(user, TokenType.EMAIL_CONFIRMATION)
        AuthToken.objects.filter(id=newer.id).update(
            created_at=timezone.now() + timedelta(minutes=1),
        )

        response = api_client.get(
            f"{URL}?email=CONFIRM-ME@example.com",
            headers={"X-E2E-Token-Secret": SECRET},
        )

        assert response.status_code == 200
        assert response.json()["token"] == newer.token
