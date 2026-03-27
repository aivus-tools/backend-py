"""Middleware security tests.

Tests cover:
- BUG-007: X-User-Group header trusted from client (should use DB value)
- BUG-008: API key uses timing-vulnerable comparison
- Invalid API key rejection
- Missing auth headers return 401
- Expired HMAC timestamp rejection
"""

import hashlib
import hmac as hmac_module
import json
import time

import pytest
from django.conf import settings
from django.test import Client as DjangoTestClient

from aivus_backend.users.models import User
from aivus_backend.users.models import Vendor


def _auth_headers(user, group=None):
    """Build API key authentication headers."""
    return {
        "HTTP_X_API_KEY": settings.API_KEY,
        "HTTP_X_USER_ID": str(user.id),
        "HTTP_X_USER_GROUP": group or user.group,
    }


def _hmac_headers(user, method, path, group=None, timestamp=None):
    """Build HMAC authentication headers."""
    ts = timestamp or str(int(time.time()))
    user_group = group or user.group
    message = f"{method}:{path}:{ts}:{user.id}:{user_group}"
    signature = hmac_module.new(
        settings.HMAC_SECRET.encode(),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()
    return {
        "HTTP_X_USER_ID": str(user.id),
        "HTTP_X_USER_GROUP": user_group,
        "HTTP_X_TIMESTAMP": ts,
        "HTTP_X_SIGNATURE": signature,
    }


@pytest.fixture
def api_client():
    return DjangoTestClient()


@pytest.fixture
def vendor_user(db):
    """Create a VENDOR user."""
    user = User.objects.create_user(
        email="mw-vendor@example.com",
        password="testpass123",
        name="MW Vendor",
        group="VENDOR",
    )
    Vendor.objects.create(name="MW Agency", owner=user)
    return user


@pytest.fixture
def client_user(db):
    """Create a CLIENT user."""
    return User.objects.create_user(
        email="mw-client@example.com",
        password="testpass123",
        name="MW Client",
        group="CLIENT",
    )


# ==================== BUG-007: X-User-Group header spoofing ====================


class TestUserGroupHeaderSpoofing:
    """BUG-007: Middleware should NOT trust X-User-Group from the client.

    The middleware should look up the user's group from the database,
    not use whatever the client sends in the X-User-Group header.
    """

    def test_vendor_spoofing_system_group_denied(self, api_client, vendor_user):
        """VENDOR sending X-User-Group: SYSTEM should be treated as VENDOR.

        The user_data.group should reflect the DB value, not the header.
        The /api/v1/users endpoint requires SYSTEM group, so a VENDOR
        spoofing SYSTEM should still be denied.
        """
        headers = _auth_headers(vendor_user, "SYSTEM")
        response = api_client.get("/api/v1/users", **headers)
        # Should be 403 because the user is actually VENDOR, not SYSTEM
        assert response.status_code == 403

    def test_client_spoofing_vendor_group(self, api_client, client_user):
        """CLIENT sending X-User-Group: VENDOR should be treated as CLIENT.

        The /api/v1/users endpoint requires SYSTEM group. A CLIENT user
        spoofing VENDOR should still not gain SYSTEM access. We verify that
        the user_data reflects the DB group, not the spoofed header.
        """
        import uuid

        fake_vendor_id = str(uuid.uuid4())
        headers = {
            "HTTP_X_API_KEY": settings.API_KEY,
            "HTTP_X_USER_ID": str(client_user.id),
            "HTTP_X_USER_GROUP": "VENDOR",
            "HTTP_X_VENDOR_ID": fake_vendor_id,
        }
        # The /api/v1/users/me endpoint returns the user's actual group from DB
        response = api_client.get("/api/v1/users/me", **headers)
        assert response.status_code == 200
        data = json.loads(response.content)
        # BUG-007: group should be CLIENT (DB), not VENDOR (header)
        assert data["group"] == "CLIENT"

    def test_middleware_sets_correct_group_from_db(self, api_client, vendor_user):
        """After middleware processes, request.user_data.group should match DB."""
        headers = _auth_headers(vendor_user, "VENDOR")
        response = api_client.get("/api/v1/users/me", **headers)
        assert response.status_code == 200
        data = json.loads(response.content)
        # The me endpoint returns user.group from DB
        assert data["group"] == "VENDOR"


# ==================== BUG-008: API key timing-safe comparison ====================


class TestAPIKeyComparison:
    """BUG-008: API key should use constant-time comparison.

    This is a code-level bug — we can't directly test timing, but we can
    verify that wrong API keys are properly rejected.
    """

    def test_wrong_api_key_rejected(self, api_client, vendor_user):
        """Request with wrong API key should return 401."""
        headers = {
            "HTTP_X_API_KEY": "wrong-api-key-12345",
            "HTTP_X_USER_ID": str(vendor_user.id),
            "HTTP_X_USER_GROUP": "VENDOR",
        }
        response = api_client.get("/api/v1/users/me", **headers)
        assert response.status_code == 401

    def test_empty_api_key_rejected(self, api_client, vendor_user):
        """Request with empty API key should return 401."""
        headers = {
            "HTTP_X_API_KEY": "",
            "HTTP_X_USER_ID": str(vendor_user.id),
            "HTTP_X_USER_GROUP": "VENDOR",
        }
        response = api_client.get("/api/v1/users/me", **headers)
        assert response.status_code == 401

    def test_partial_api_key_rejected(self, api_client, vendor_user):
        """Request with partial API key should return 401."""
        partial = settings.API_KEY[:5]
        headers = {
            "HTTP_X_API_KEY": partial,
            "HTTP_X_USER_ID": str(vendor_user.id),
            "HTTP_X_USER_GROUP": "VENDOR",
        }
        response = api_client.get("/api/v1/users/me", **headers)
        assert response.status_code == 401

    def test_correct_api_key_accepted(self, api_client, vendor_user):
        """Request with correct API key should succeed."""
        headers = _auth_headers(vendor_user, "VENDOR")
        response = api_client.get("/api/v1/users/me", **headers)
        assert response.status_code == 200


# ==================== Missing auth headers ====================


class TestMissingAuthHeaders:
    """Test that missing auth headers return 401."""

    def test_no_headers_at_all(self, api_client):
        """Request with no auth headers should return 401."""
        response = api_client.get("/api/v1/users/me")
        assert response.status_code in (401, 403)

    def test_api_key_without_user_id(self, api_client):
        """API key without X-User-Id should return 401."""
        headers = {
            "HTTP_X_API_KEY": settings.API_KEY,
            "HTTP_X_USER_GROUP": "VENDOR",
        }
        response = api_client.get("/api/v1/users/me", **headers)
        assert response.status_code in (401, 403)

    def test_api_key_without_user_group(self, api_client, vendor_user):
        """API key without X-User-Group should deny access or fall through to HMAC."""
        headers = {
            "HTTP_X_API_KEY": settings.API_KEY,
            "HTTP_X_USER_ID": str(vendor_user.id),
        }
        response = api_client.get("/api/v1/users/me", **headers)
        # Without user_group, middleware should either reject (401) or
        # fall through — but the view's own auth check should catch it
        assert response.status_code in (200, 401, 403)
        if response.status_code == 200:
            # If middleware allowed through, verify user_data was set correctly
            data = json.loads(response.content)
            assert data["id"] == str(vendor_user.id)

    def test_nonexistent_user_id_rejected(self, api_client, db):
        """API key with non-existent user ID should return 401."""
        import uuid

        headers = {
            "HTTP_X_API_KEY": settings.API_KEY,
            "HTTP_X_USER_ID": str(uuid.uuid4()),
            "HTTP_X_USER_GROUP": "VENDOR",
        }
        response = api_client.get("/api/v1/users/me", **headers)
        assert response.status_code == 401


# ==================== HMAC timestamp validation ====================


class TestHMACTimestamp:
    """Test HMAC timestamp validation (5-minute tolerance)."""

    def test_valid_hmac_signature_accepted(self, api_client, vendor_user):
        """Valid HMAC signature with current timestamp should work."""
        headers = _hmac_headers(vendor_user, "GET", "/api/v1/users/me")
        response = api_client.get("/api/v1/users/me", **headers)
        assert response.status_code == 200

    def test_expired_timestamp_rejected(self, api_client, vendor_user):
        """HMAC with timestamp > 5 minutes old should be rejected."""
        old_timestamp = str(int(time.time()) - 600)  # 10 minutes ago
        headers = _hmac_headers(
            vendor_user, "GET", "/api/v1/users/me", timestamp=old_timestamp
        )
        response = api_client.get("/api/v1/users/me", **headers)
        assert response.status_code == 401

    def test_future_timestamp_rejected(self, api_client, vendor_user):
        """HMAC with timestamp far in the future should be rejected."""
        future_timestamp = str(int(time.time()) + 600)  # 10 minutes ahead
        headers = _hmac_headers(
            vendor_user, "GET", "/api/v1/users/me", timestamp=future_timestamp
        )
        response = api_client.get("/api/v1/users/me", **headers)
        assert response.status_code == 401

    def test_invalid_timestamp_format_rejected(self, api_client, vendor_user):
        """HMAC with non-numeric timestamp should be rejected."""
        headers = {
            "HTTP_X_USER_ID": str(vendor_user.id),
            "HTTP_X_USER_GROUP": "VENDOR",
            "HTTP_X_TIMESTAMP": "not-a-number",
            "HTTP_X_SIGNATURE": "fake-signature",
        }
        response = api_client.get("/api/v1/users/me", **headers)
        assert response.status_code == 401

    def test_wrong_hmac_signature_rejected(self, api_client, vendor_user):
        """HMAC with wrong signature should be rejected."""
        ts = str(int(time.time()))
        headers = {
            "HTTP_X_USER_ID": str(vendor_user.id),
            "HTTP_X_USER_GROUP": "VENDOR",
            "HTTP_X_TIMESTAMP": ts,
            "HTTP_X_SIGNATURE": "incorrect-signature-value",
        }
        response = api_client.get("/api/v1/users/me", **headers)
        assert response.status_code == 401


# ==================== Public endpoints skip auth ====================


class TestPublicEndpoints:
    """Test that public endpoints work without authentication."""

    def test_register_is_public(self, api_client, db):
        """Registration endpoint should not require auth."""
        response = api_client.post(
            "/api/v1/auth/register",
            data=json.dumps(
                {
                    "email": "public-test@example.com",
                    "password": "validpass123",
                    "name": "Public Test",
                    "authType": "CREDENTIALS",
                }
            ),
            content_type="application/json",
        )
        # Should not return 401 (it's public)
        assert response.status_code != 401

    def test_login_is_public(self, api_client, db):
        """Login endpoint should not require auth.

        The endpoint is public, but returns 401 for invalid credentials.
        We verify it returns a meaningful auth response, not a middleware 401.
        """
        response = api_client.post(
            "/api/v1/auth/login",
            data=json.dumps(
                {
                    "email": "nobody@example.com",
                    "password": "whatever",
                }
            ),
            content_type="application/json",
        )
        # 401 from the view (invalid credentials) is different from
        # 401 from middleware (missing auth). The error message tells us:
        data = json.loads(response.content)
        assert "error" in data
        # Should say "Invalid credentials" (view), not "Missing..." (mw)
        assert "credentials" in data["error"].lower() or "Missing" not in data.get(
            "error", ""
        )

    def test_forgot_password_is_public(self, api_client, db):
        """Forgot password endpoint should not require auth."""
        response = api_client.post(
            "/api/v1/auth/forgot-password",
            data=json.dumps({"email": "nobody@example.com"}),
            content_type="application/json",
        )
        assert response.status_code != 401
