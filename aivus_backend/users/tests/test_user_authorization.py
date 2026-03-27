"""Authorization tests for user endpoints.

Tests cover:
- BUG-002: change_group only works for self (no privilege escalation)
- BUG-002: change_group validates group values
- BUG-002: change_group only allows CONFIRMED -> VENDOR/CLIENT
- BUG-010: get_users restricted to SYSTEM only
- BUG-049: profile/settings input length validation
"""

import json

import pytest
from django.conf import settings
from django.test import Client as DjangoTestClient

from aivus_backend.users.models import Client as ClientModel
from aivus_backend.users.models import User
from aivus_backend.users.models import Vendor


def _auth_headers(user, group=None):
    """Build authentication headers for API requests."""
    return {
        "HTTP_X_API_KEY": settings.API_KEY,
        "HTTP_X_USER_ID": str(user.id),
        "HTTP_X_USER_GROUP": group or user.group,
    }


@pytest.fixture
def api_client():
    return DjangoTestClient()


@pytest.fixture
def confirmed_user(db):
    """A CONFIRMED user (has confirmed email, hasn't chosen role)."""
    return User.objects.create_user(
        email="confirmed@example.com",
        password="testpass123",
        name="Confirmed User",
        group="CONFIRMED",
    )


@pytest.fixture
def vendor_user(db):
    """A VENDOR user."""
    user = User.objects.create_user(
        email="vendor@example.com",
        password="testpass123",
        name="Vendor User",
        group="VENDOR",
    )
    Vendor.objects.create(name="Test Vendor", owner=user)
    return user


@pytest.fixture
def client_user(db):
    """A CLIENT user."""
    user = User.objects.create_user(
        email="client@example.com",
        password="testpass123",
        name="Client User",
        group="CLIENT",
    )
    ClientModel.objects.create(name="Test Client", ein="123", owner=user)
    return user


@pytest.fixture
def system_user(db):
    """A SYSTEM user."""
    return User.objects.create_user(
        email="system@example.com",
        password="testpass123",
        name="System User",
        group="SYSTEM",
    )


@pytest.fixture
def second_confirmed_user(db):
    """A second CONFIRMED user (for IDOR tests)."""
    return User.objects.create_user(
        email="confirmed2@example.com",
        password="testpass123",
        name="Confirmed User 2",
        group="CONFIRMED",
    )


# ==================== BUG-002: change_group self-only ====================


class TestChangeGroupSelfOnly:
    """BUG-002: Users should only be able to change their OWN group.

    Previously, any user could change ANY user's group via the user_id URL param.
    """

    def test_change_own_group_to_vendor(self, api_client, confirmed_user):
        """CONFIRMED user should be able to change own group to VENDOR."""
        headers = _auth_headers(confirmed_user, "CONFIRMED")
        response = api_client.patch(
            f"/api/v1/users/{confirmed_user.id}/change-group",
            data=json.dumps({"group": "VENDOR"}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["group"] == "VENDOR"

    def test_change_own_group_to_client(self, api_client, confirmed_user):
        """CONFIRMED user should be able to change own group to CLIENT."""
        headers = _auth_headers(confirmed_user, "CONFIRMED")
        response = api_client.patch(
            f"/api/v1/users/{confirmed_user.id}/change-group",
            data=json.dumps({"group": "CLIENT"}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["group"] == "CLIENT"

    def test_cannot_change_other_users_group(
        self, api_client, confirmed_user, second_confirmed_user
    ):
        """User A should NOT be able to change User B's group."""
        # confirmed_user tries to change second_confirmed_user's group
        headers = _auth_headers(confirmed_user, "CONFIRMED")
        response = api_client.patch(
            f"/api/v1/users/{second_confirmed_user.id}/change-group",
            data=json.dumps({"group": "VENDOR"}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 403
        # Verify user B's group was NOT changed
        second_confirmed_user.refresh_from_db()
        assert second_confirmed_user.group == "CONFIRMED"

    def test_vendor_cannot_escalate_other_to_system(
        self, api_client, vendor_user, second_confirmed_user
    ):
        """VENDOR should NOT be able to escalate another user to SYSTEM."""
        headers = _auth_headers(vendor_user, "VENDOR")
        response = api_client.patch(
            f"/api/v1/users/{second_confirmed_user.id}/change-group",
            data=json.dumps({"group": "SYSTEM"}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 403
        second_confirmed_user.refresh_from_db()
        assert second_confirmed_user.group == "CONFIRMED"


# ==================== BUG-002: change_group validates values ====================


class TestChangeGroupValidation:
    """change_group must validate group values — no arbitrary strings."""

    def test_invalid_group_value_rejected(self, api_client, confirmed_user):
        """Arbitrary group string should be rejected."""
        headers = _auth_headers(confirmed_user, "CONFIRMED")
        response = api_client.patch(
            f"/api/v1/users/{confirmed_user.id}/change-group",
            data=json.dumps({"group": "SUPERADMIN"}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 400
        confirmed_user.refresh_from_db()
        assert confirmed_user.group == "CONFIRMED"

    def test_empty_group_rejected(self, api_client, confirmed_user):
        """Empty group value should be rejected."""
        headers = _auth_headers(confirmed_user, "CONFIRMED")
        response = api_client.patch(
            f"/api/v1/users/{confirmed_user.id}/change-group",
            data=json.dumps({"group": ""}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 400

    def test_no_group_field_rejected(self, api_client, confirmed_user):
        """Missing group field should be rejected."""
        headers = _auth_headers(confirmed_user, "CONFIRMED")
        response = api_client.patch(
            f"/api/v1/users/{confirmed_user.id}/change-group",
            data=json.dumps({}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 400


# ==================== BUG-002: change_group transition rules ====================


class TestChangeGroupTransitions:
    """change_group only allows CONFIRMED -> VENDOR or CONFIRMED -> CLIENT."""

    def test_vendor_cannot_change_to_client(self, api_client, vendor_user):
        """VENDOR user should NOT be able to change to CLIENT."""
        headers = _auth_headers(vendor_user, "VENDOR")
        response = api_client.patch(
            f"/api/v1/users/{vendor_user.id}/change-group",
            data=json.dumps({"group": "CLIENT"}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 400
        vendor_user.refresh_from_db()
        assert vendor_user.group == "VENDOR"

    def test_client_cannot_change_to_vendor(self, api_client, client_user):
        """CLIENT user should NOT be able to change to VENDOR."""
        headers = _auth_headers(client_user, "CLIENT")
        response = api_client.patch(
            f"/api/v1/users/{client_user.id}/change-group",
            data=json.dumps({"group": "VENDOR"}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 400
        client_user.refresh_from_db()
        assert client_user.group == "CLIENT"

    def test_confirmed_cannot_change_to_system(self, api_client, confirmed_user):
        """CONFIRMED user should NOT be able to change to SYSTEM."""
        headers = _auth_headers(confirmed_user, "CONFIRMED")
        response = api_client.patch(
            f"/api/v1/users/{confirmed_user.id}/change-group",
            data=json.dumps({"group": "SYSTEM"}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 400
        confirmed_user.refresh_from_db()
        assert confirmed_user.group == "CONFIRMED"

    def test_confirmed_cannot_change_to_unconfirmed(self, api_client, confirmed_user):
        """CONFIRMED user should NOT be able to revert to UNCONFIRMED."""
        headers = _auth_headers(confirmed_user, "CONFIRMED")
        response = api_client.patch(
            f"/api/v1/users/{confirmed_user.id}/change-group",
            data=json.dumps({"group": "UNCONFIRMED"}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 400
        confirmed_user.refresh_from_db()
        assert confirmed_user.group == "CONFIRMED"


# ==================== BUG-010: get_users restricted to SYSTEM ====================


class TestGetUsersRestricted:
    """BUG-010: /api/v1/users should be restricted to SYSTEM users only.

    Previously, any authenticated user could see all users' data.
    """

    def test_system_user_can_list_users(self, api_client, system_user, vendor_user):
        """SYSTEM user should be able to list all users."""
        headers = _auth_headers(system_user, "SYSTEM")
        response = api_client.get("/api/v1/users", **headers)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert isinstance(data, list)
        assert len(data) >= 2  # At least system + vendor

    def test_vendor_cannot_list_users(self, api_client, vendor_user):
        """VENDOR user should NOT be able to list all users."""
        headers = _auth_headers(vendor_user, "VENDOR")
        response = api_client.get("/api/v1/users", **headers)
        assert response.status_code == 403

    def test_client_cannot_list_users(self, api_client, client_user):
        """CLIENT user should NOT be able to list all users."""
        headers = _auth_headers(client_user, "CLIENT")
        response = api_client.get("/api/v1/users", **headers)
        assert response.status_code == 403

    def test_confirmed_cannot_list_users(self, api_client, confirmed_user):
        """CONFIRMED user should NOT be able to list all users."""
        headers = _auth_headers(confirmed_user, "CONFIRMED")
        response = api_client.get("/api/v1/users", **headers)
        assert response.status_code == 403

    def test_unauthenticated_cannot_list_users(self, api_client):
        """Unauthenticated request should NOT be able to list users."""
        response = api_client.get("/api/v1/users")
        assert response.status_code in (401, 403)


# ==================== BUG-049: Profile/settings length validation ====================


class TestProfileInputLengthValidation:
    """BUG-049: Profile and settings must validate input lengths.

    Previously, names >255 chars caused 500 with DB column info leaked.
    """

    def test_profile_name_too_long_returns_400(self, api_client, vendor_user):
        """Name longer than 255 chars should return 400, not 500."""
        headers = _auth_headers(vendor_user, "VENDOR")
        long_name = "A" * 256
        response = api_client.patch(
            "/api/v1/users/profile",
            data=json.dumps({"name": long_name}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 400

    def test_profile_name_at_max_length_succeeds(self, api_client, vendor_user):
        """Name of exactly 255 chars should succeed."""
        headers = _auth_headers(vendor_user, "VENDOR")
        max_name = "A" * 255
        response = api_client.patch(
            "/api/v1/users/profile",
            data=json.dumps({"name": max_name}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 200

    def test_settings_language_too_long_returns_400(self, api_client, vendor_user):
        """Language > 5 chars should return 400, not 500."""
        headers = _auth_headers(vendor_user, "VENDOR")
        response = api_client.patch(
            "/api/v1/users/settings",
            data=json.dumps({"language": "toolong"}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 400
