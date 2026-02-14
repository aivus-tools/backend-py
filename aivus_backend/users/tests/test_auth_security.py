"""Security tests for authentication endpoints.

Tests cover:
- BUG-001: Google OAuth login bypass
- BUG-003: Registration allows empty/short password
- BUG-019: User enumeration via forgot_password
- BUG-034: reset_password missing minimum password length
- Login with correct/incorrect credentials
"""

import json

import pytest
from django.conf import settings
from django.test import Client as DjangoTestClient

from aivus_backend.users.models import User
from aivus_backend.users.tokens import AuthToken
from aivus_backend.users.tokens import TokenType


@pytest.fixture
def api_client():
    return DjangoTestClient()


@pytest.fixture
def credentials_user(db):
    """Create a user with CREDENTIALS auth type and known password."""
    return User.objects.create_user(
        email="creds-user@example.com",
        password="securepass123",
        name="Credentials User",
        group="CONFIRMED",
        auth_type="CREDENTIALS",
    )


@pytest.fixture
def google_user(db):
    """Create a user with GOOGLE auth type."""
    return User.objects.create_user(
        email="google-user@example.com",
        password="temppass12345",
        name="Google User",
        group="CONFIRMED",
        auth_type="GOOGLE",
    )


# ==================== BUG-001: Google OAuth login bypass ====================


class TestGoogleOAuthLoginBypass:
    """BUG-001: Google OAuth login should NOT bypass password verification.

    Previously, sending authType=GOOGLE with just an email would log in
    without any token/password verification. The fix should reject direct
    Google login and require proper OAuth flow.
    """

    def test_google_auth_type_login_is_rejected(self, api_client, google_user):
        """Sending authType=GOOGLE should be rejected — not allow direct login."""
        response = api_client.post(
            "/api/v1/auth/login",
            data=json.dumps({
                "email": google_user.email,
                "authType": "GOOGLE",
            }),
            content_type="application/json",
        )
        assert response.status_code == 400
        data = json.loads(response.content)
        assert "OAuth" in data.get("error", "") or "Google" in data.get("error", "")

    def test_google_auth_no_token_no_login(self, api_client, google_user):
        """Google auth without OAuth token should never return user data."""
        response = api_client.post(
            "/api/v1/auth/login",
            data=json.dumps({
                "email": google_user.email,
                "authType": "GOOGLE",
            }),
            content_type="application/json",
        )
        data = json.loads(response.content)
        # Should NOT return user id or email on failed auth
        assert "id" not in data
        assert "email" not in data

    def test_google_auth_with_arbitrary_email_rejected(self, api_client, google_user):
        """Attacker using Google auth type with victim's email must be rejected."""
        response = api_client.post(
            "/api/v1/auth/login",
            data=json.dumps({
                "email": google_user.email,
                "authType": "GOOGLE",
                "password": "",
            }),
            content_type="application/json",
        )
        # Must NOT return 200 with user data
        assert response.status_code != 200 or "id" not in json.loads(response.content)


# ==================== BUG-003: Registration password validation ====================


class TestRegistrationPasswordValidation:
    """BUG-003: Registration must require password >= 8 characters.

    Previously, empty password was accepted via make_password("").
    """

    def test_register_empty_password_rejected(self, api_client, db):
        """Registration with empty password should return 400."""
        response = api_client.post(
            "/api/v1/auth/register",
            data=json.dumps({
                "email": "newuser@example.com",
                "password": "",
                "name": "New User",
                "authType": "CREDENTIALS",
            }),
            content_type="application/json",
        )
        assert response.status_code == 400
        data = json.loads(response.content)
        assert "password" in data.get("error", "").lower() or "8" in data.get("error", "")

    def test_register_short_password_rejected(self, api_client, db):
        """Registration with password < 8 chars should return 400."""
        response = api_client.post(
            "/api/v1/auth/register",
            data=json.dumps({
                "email": "shortpwd@example.com",
                "password": "abc",
                "name": "Short Pwd User",
                "authType": "CREDENTIALS",
            }),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_register_no_password_field_rejected(self, api_client, db):
        """Registration without password field should return 400."""
        response = api_client.post(
            "/api/v1/auth/register",
            data=json.dumps({
                "email": "nopwd@example.com",
                "name": "No Pwd User",
                "authType": "CREDENTIALS",
            }),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_register_valid_password_succeeds(self, api_client, db):
        """Registration with password >= 8 chars should succeed."""
        response = api_client.post(
            "/api/v1/auth/register",
            data=json.dumps({
                "email": "goodpwd@example.com",
                "password": "validpass123",
                "name": "Good Pwd User",
                "authType": "CREDENTIALS",
            }),
            content_type="application/json",
        )
        assert response.status_code == 201

    def test_register_7_char_password_rejected(self, api_client, db):
        """Registration with exactly 7 chars should be rejected (boundary)."""
        response = api_client.post(
            "/api/v1/auth/register",
            data=json.dumps({
                "email": "boundary@example.com",
                "password": "1234567",
                "name": "Boundary User",
                "authType": "CREDENTIALS",
            }),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_register_8_char_password_accepted(self, api_client, db):
        """Registration with a strong 8+ char password should succeed."""
        response = api_client.post(
            "/api/v1/auth/register",
            data=json.dumps({
                "email": "eightchar@example.com",
                "password": "Str0ngP@ss",
                "name": "Eight Char User",
                "authType": "CREDENTIALS",
            }),
            content_type="application/json",
        )
        assert response.status_code == 201


# ==================== BUG-019: User enumeration via forgot_password ====================


class TestForgotPasswordEnumeration:
    """BUG-019: forgot_password must not reveal whether email exists.

    Previously, it returned 404 for non-existent emails and 200 for existing ones.
    The fix should return 200 with a generic message in both cases.
    """

    def test_forgot_password_existing_email_returns_200(self, api_client, credentials_user):
        """Existing email should return 200 with generic message."""
        response = api_client.post(
            "/api/v1/auth/forgot-password",
            data=json.dumps({"email": credentials_user.email}),
            content_type="application/json",
        )
        assert response.status_code == 200
        data = json.loads(response.content)
        assert "message" in data

    def test_forgot_password_nonexistent_email_returns_200(self, api_client, db):
        """Non-existent email should also return 200 (no enumeration)."""
        response = api_client.post(
            "/api/v1/auth/forgot-password",
            data=json.dumps({"email": "nonexistent@example.com"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        data = json.loads(response.content)
        assert "message" in data

    def test_forgot_password_same_response_shape(self, api_client, credentials_user, db):
        """Both existing and non-existing emails must return identical response structure."""
        response_existing = api_client.post(
            "/api/v1/auth/forgot-password",
            data=json.dumps({"email": credentials_user.email}),
            content_type="application/json",
        )
        response_nonexistent = api_client.post(
            "/api/v1/auth/forgot-password",
            data=json.dumps({"email": "nobody@nowhere.com"}),
            content_type="application/json",
        )
        data_existing = json.loads(response_existing.content)
        data_nonexistent = json.loads(response_nonexistent.content)
        # Both should have the same keys (no extra info for existing user)
        assert set(data_existing.keys()) == set(data_nonexistent.keys())
        # Status codes must match
        assert response_existing.status_code == response_nonexistent.status_code


# ==================== BUG-034: reset_password minimum password length ====================


class TestResetPasswordValidation:
    """BUG-034: reset_password must enforce minimum password length of 8 chars.

    Previously, any password including "1" was accepted.
    """

    @pytest.fixture
    def reset_token(self, credentials_user):
        """Create a valid password reset token."""
        return AuthToken.create_token(credentials_user, TokenType.PASSWORD_RESET)

    def test_reset_password_short_password_rejected(self, api_client, reset_token):
        """Reset with password < 8 chars should return 400."""
        response = api_client.post(
            f"/api/v1/auth/reset-password?token={reset_token.token}",
            data=json.dumps({"password": "short"}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_reset_password_single_char_rejected(self, api_client, reset_token):
        """Reset with single character password should return 400."""
        response = api_client.post(
            f"/api/v1/auth/reset-password?token={reset_token.token}",
            data=json.dumps({"password": "1"}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_reset_password_empty_rejected(self, api_client, reset_token):
        """Reset with empty password should return 400."""
        response = api_client.post(
            f"/api/v1/auth/reset-password?token={reset_token.token}",
            data=json.dumps({"password": ""}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_reset_password_valid_length_succeeds(self, api_client, reset_token):
        """Reset with password >= 8 chars should succeed."""
        response = api_client.post(
            f"/api/v1/auth/reset-password?token={reset_token.token}",
            data=json.dumps({"password": "newvalid123"}),
            content_type="application/json",
        )
        assert response.status_code == 200

    def test_reset_password_no_token_rejected(self, api_client, db):
        """Reset without token should return 400."""
        response = api_client.post(
            "/api/v1/auth/reset-password",
            data=json.dumps({"password": "newvalid123"}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_reset_password_invalid_token_rejected(self, api_client, db):
        """Reset with fake token should return 400."""
        response = api_client.post(
            "/api/v1/auth/reset-password?token=invalid-fake-token",
            data=json.dumps({"password": "newvalid123"}),
            content_type="application/json",
        )
        assert response.status_code == 400


# ==================== Login correctness ====================


class TestLoginCredentials:
    """Test login with correct and incorrect credentials."""

    def test_login_correct_credentials(self, api_client, credentials_user):
        """Login with correct email/password should return 200 with user data."""
        response = api_client.post(
            "/api/v1/auth/login",
            data=json.dumps({
                "email": credentials_user.email,
                "password": "securepass123",
                "authType": "CREDENTIALS",
            }),
            content_type="application/json",
        )
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["id"] == str(credentials_user.id)
        assert data["email"] == credentials_user.email

    def test_login_wrong_password(self, api_client, credentials_user):
        """Login with wrong password should return 401."""
        response = api_client.post(
            "/api/v1/auth/login",
            data=json.dumps({
                "email": credentials_user.email,
                "password": "wrongpassword",
                "authType": "CREDENTIALS",
            }),
            content_type="application/json",
        )
        assert response.status_code == 401

    def test_login_nonexistent_email(self, api_client, db):
        """Login with non-existent email should return 401."""
        response = api_client.post(
            "/api/v1/auth/login",
            data=json.dumps({
                "email": "nonexistent@example.com",
                "password": "anypassword",
                "authType": "CREDENTIALS",
            }),
            content_type="application/json",
        )
        assert response.status_code == 401

    def test_login_missing_password(self, api_client, credentials_user):
        """Login without password should return 400."""
        response = api_client.post(
            "/api/v1/auth/login",
            data=json.dumps({
                "email": credentials_user.email,
                "authType": "CREDENTIALS",
            }),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_login_missing_email(self, api_client, db):
        """Login without email should return 400."""
        response = api_client.post(
            "/api/v1/auth/login",
            data=json.dumps({
                "password": "somepassword",
                "authType": "CREDENTIALS",
            }),
            content_type="application/json",
        )
        assert response.status_code == 400
