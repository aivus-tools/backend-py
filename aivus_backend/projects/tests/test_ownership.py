"""IDOR protection tests for project/offer/brief ownership.

Tests cover:
- BUG-004: Vendor A cannot access vendor B's project
- BUG-005: Vendor A cannot access vendor B's offer
- BUG-006: Client A cannot access client B's brief
- BUG-017: Offers list filtered by vendor
- BUG-018: Briefs list filtered by client
- BUG-031: Share creation requires ownership
"""

import json

import pytest
from django.conf import settings
from django.test import Client as DjangoTestClient
from django.utils import timezone

from aivus_backend.users.models import Client as ClientModel
from aivus_backend.users.models import User
from aivus_backend.users.models import Vendor
from aivus_backend.projects.models import Brief
from aivus_backend.projects.models import Offer
from aivus_backend.projects.models import Project


def _auth_headers(user, group=None):
    """Build authentication headers."""
    return {
        "HTTP_X_API_KEY": settings.API_KEY,
        "HTTP_X_USER_ID": str(user.id),
        "HTTP_X_USER_GROUP": group or user.group,
    }


def _vendor_headers(user, vendor_id=None):
    """Build vendor auth headers including X-Vendor-Id."""
    headers = _auth_headers(user, "VENDOR")
    if vendor_id:
        headers["HTTP_X_VENDOR_ID"] = str(vendor_id)
    return headers


@pytest.fixture
def api_client():
    return DjangoTestClient()


@pytest.fixture
def vendor_user_a(db):
    """Vendor user A."""
    user = User.objects.create_user(
        email="vendor-a@example.com",
        password="testpass123",
        name="Vendor A",
        group="VENDOR",
    )
    return user


@pytest.fixture
def vendor_a(vendor_user_a):
    """Vendor A company."""
    return Vendor.objects.create(name="Agency A", owner=vendor_user_a)


@pytest.fixture
def vendor_user_b(db):
    """Vendor user B."""
    user = User.objects.create_user(
        email="vendor-b@example.com",
        password="testpass123",
        name="Vendor B",
        group="VENDOR",
    )
    return user


@pytest.fixture
def vendor_b(vendor_user_b):
    """Vendor B company."""
    return Vendor.objects.create(name="Agency B", owner=vendor_user_b)


@pytest.fixture
def client_user_a(db):
    """Client user A."""
    user = User.objects.create_user(
        email="client-a@example.com",
        password="testpass123",
        name="Client A",
        group="CLIENT",
    )
    return user


@pytest.fixture
def client_a(client_user_a):
    """Client A company."""
    return ClientModel.objects.create(name="Company A", ein="111", owner=client_user_a)


@pytest.fixture
def client_user_b(db):
    """Client user B."""
    user = User.objects.create_user(
        email="client-b@example.com",
        password="testpass123",
        name="Client B",
        group="CLIENT",
    )
    return user


@pytest.fixture
def client_b(client_user_b):
    """Client B company."""
    return ClientModel.objects.create(name="Company B", ein="222", owner=client_user_b)


@pytest.fixture
def project_a(vendor_a):
    """Project owned by Vendor A."""
    return Project.objects.create(
        name="Project A",
        vendor=vendor_a,
        status="DRAFT",
    )


@pytest.fixture
def project_b(vendor_b):
    """Project owned by Vendor B."""
    return Project.objects.create(
        name="Project B",
        vendor=vendor_b,
        status="DRAFT",
    )


@pytest.fixture
def offer_a(project_a):
    """Offer under Vendor A's project."""
    return Offer.objects.create(
        project_name="Offer A",
        project=project_a,
        status="DRAFT",
        details={"offers": []},
        deadline=timezone.now(),
        source="PLATFORM",
    )


@pytest.fixture
def offer_b(project_b):
    """Offer under Vendor B's project."""
    return Offer.objects.create(
        project_name="Offer B",
        project=project_b,
        status="DRAFT",
        details={"offers": []},
        deadline=timezone.now(),
        source="PLATFORM",
    )


@pytest.fixture
def brief_a(client_a):
    """Brief owned by Client A."""
    return Brief.objects.create(
        status="DRAFT",
        details={"projectName": "Brief A"},
        client=client_a,
    )


@pytest.fixture
def brief_b(client_b):
    """Brief owned by Client B."""
    return Brief.objects.create(
        status="DRAFT",
        details={"projectName": "Brief B"},
        client=client_b,
    )


# ==================== BUG-004: Project IDOR ====================


class TestProjectOwnership:
    """BUG-004: Vendor A should not be able to modify/delete Vendor B's project.

    Previously, project_detail had no vendor ownership check.
    """

    def test_vendor_a_can_access_own_project(self, api_client, vendor_user_a, vendor_a, project_a):
        """Vendor A should be able to GET their own project."""
        headers = _vendor_headers(vendor_user_a, vendor_a.id)
        response = api_client.get(
            f"/api/v1/projects/{project_a.id}", **headers
        )
        assert response.status_code == 200

    def test_vendor_b_cannot_modify_vendor_a_project(
        self, api_client, vendor_user_b, vendor_b, project_a
    ):
        """Vendor B should NOT be able to PATCH Vendor A's project."""
        headers = _vendor_headers(vendor_user_b, vendor_b.id)
        response = api_client.patch(
            f"/api/v1/projects/{project_a.id}",
            data=json.dumps({"name": "Hacked Name"}),
            content_type="application/json",
            **headers,
        )
        # Should be 403 after fix (currently returns 200 = BUG-004)
        assert response.status_code in (403, 404)
        # Verify project was NOT modified
        project_a.refresh_from_db()
        assert project_a.name == "Project A"

    def test_vendor_b_cannot_delete_vendor_a_project(
        self, api_client, vendor_user_b, vendor_b, project_a
    ):
        """Vendor B should NOT be able to DELETE Vendor A's project."""
        headers = _vendor_headers(vendor_user_b, vendor_b.id)
        response = api_client.delete(
            f"/api/v1/projects/{project_a.id}", **headers
        )
        assert response.status_code in (403, 404)
        project_a.refresh_from_db()
        assert project_a.deleted_at is None

    def test_client_cannot_modify_vendor_project(
        self, api_client, client_user_a, client_a, project_a
    ):
        """CLIENT should NOT be able to modify a VENDOR's project."""
        headers = _auth_headers(client_user_a, "CLIENT")
        response = api_client.patch(
            f"/api/v1/projects/{project_a.id}",
            data=json.dumps({"name": "Client Modified"}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code in (403, 404)
        project_a.refresh_from_db()
        assert project_a.name == "Project A"


# ==================== BUG-005: Offer IDOR ====================


class TestOfferOwnership:
    """BUG-005: Vendor A should not be able to modify/delete Vendor B's offer.

    Previously, offer_detail had no ownership verification.
    """

    def test_vendor_a_can_access_own_offer(
        self, api_client, vendor_user_a, vendor_a, offer_a
    ):
        """Vendor A should be able to GET their own offer."""
        headers = _vendor_headers(vendor_user_a, vendor_a.id)
        response = api_client.get(
            f"/api/v1/offers/{offer_a.id}", **headers
        )
        assert response.status_code == 200

    def test_vendor_b_cannot_modify_vendor_a_offer(
        self, api_client, vendor_user_b, vendor_b, offer_a
    ):
        """Vendor B should NOT be able to PATCH Vendor A's offer."""
        headers = _vendor_headers(vendor_user_b, vendor_b.id)
        response = api_client.patch(
            f"/api/v1/offers/{offer_a.id}",
            data=json.dumps({"projectName": "Stolen Offer", "cost": 99999}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code in (403, 404)
        offer_a.refresh_from_db()
        assert offer_a.project_name == "Offer A"

    def test_vendor_b_cannot_delete_vendor_a_offer(
        self, api_client, vendor_user_b, vendor_b, offer_a
    ):
        """Vendor B should NOT be able to DELETE Vendor A's offer."""
        headers = _vendor_headers(vendor_user_b, vendor_b.id)
        response = api_client.delete(
            f"/api/v1/offers/{offer_a.id}", **headers
        )
        assert response.status_code in (403, 404)
        offer_a.refresh_from_db()
        assert offer_a.deleted_at is None

    def test_client_cannot_modify_vendor_offer(
        self, api_client, client_user_a, client_a, offer_a
    ):
        """CLIENT should NOT be able to modify a VENDOR's offer."""
        headers = _auth_headers(client_user_a, "CLIENT")
        response = api_client.patch(
            f"/api/v1/offers/{offer_a.id}",
            data=json.dumps({"cost": 1}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code in (403, 404)


# ==================== BUG-006: Brief IDOR ====================


class TestBriefOwnership:
    """BUG-006: Client A should not be able to modify/delete Client B's brief.

    Previously, brief_detail allowed any user to modify any brief.
    """

    def test_client_a_can_access_own_brief(
        self, api_client, client_user_a, client_a, brief_a
    ):
        """Client A should be able to GET their own brief."""
        headers = _auth_headers(client_user_a, "CLIENT")
        response = api_client.get(
            f"/api/v1/briefs/{brief_a.id}", **headers
        )
        assert response.status_code == 200

    def test_client_b_cannot_modify_client_a_brief(
        self, api_client, client_user_b, client_b, brief_a
    ):
        """Client B should NOT be able to PATCH Client A's brief."""
        headers = _auth_headers(client_user_b, "CLIENT")
        response = api_client.patch(
            f"/api/v1/briefs/{brief_a.id}",
            data=json.dumps({"details": {"projectName": "Hijacked Brief"}}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code in (403, 404)
        brief_a.refresh_from_db()
        assert brief_a.details["projectName"] == "Brief A"

    def test_client_b_cannot_delete_client_a_brief(
        self, api_client, client_user_b, client_b, brief_a
    ):
        """Client B should NOT be able to DELETE Client A's brief."""
        headers = _auth_headers(client_user_b, "CLIENT")
        response = api_client.delete(
            f"/api/v1/briefs/{brief_a.id}", **headers
        )
        assert response.status_code in (403, 404)
        brief_a.refresh_from_db()
        assert brief_a.deleted_at is None

    def test_vendor_cannot_modify_client_brief(
        self, api_client, vendor_user_a, vendor_a, brief_a
    ):
        """VENDOR should NOT be able to modify a CLIENT's brief."""
        headers = _auth_headers(vendor_user_a, "VENDOR")
        response = api_client.patch(
            f"/api/v1/briefs/{brief_a.id}",
            data=json.dumps({"details": {"projectName": "Vendor Modified"}}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code in (403, 404)
        brief_a.refresh_from_db()
        assert brief_a.details["projectName"] == "Brief A"


# ==================== BUG-017: Offers list filtered by vendor ====================


class TestOffersListFiltering:
    """BUG-017: Offers list should only return the requesting vendor's offers.

    Previously, when no projectId param, returned ALL offers.
    """

    def test_offers_list_without_filter_does_not_leak_others(
        self, api_client, vendor_user_a, vendor_a, offer_a, offer_b
    ):
        """Vendor A's unfiltered offers list should NOT include Vendor B's offers."""
        headers = _vendor_headers(vendor_user_a, vendor_a.id)
        response = api_client.get("/api/v1/offers", **headers)
        assert response.status_code == 200
        data = json.loads(response.content)
        offer_ids = [o["id"] for o in data]
        # Should include own offer
        assert str(offer_a.id) in offer_ids
        # Should NOT include other vendor's offer (after fix)
        assert str(offer_b.id) not in offer_ids

    def test_offers_filtered_by_project_returns_correct_offers(
        self, api_client, vendor_user_a, vendor_a, project_a, offer_a
    ):
        """Filtering by projectId should return offers for that project."""
        headers = _vendor_headers(vendor_user_a, vendor_a.id)
        response = api_client.get(
            f"/api/v1/offers?projectId={project_a.id}", **headers
        )
        assert response.status_code == 200
        data = json.loads(response.content)
        assert len(data) >= 1
        assert all(o["projectId"] == str(project_a.id) for o in data)


# ==================== BUG-018: Briefs list filtered by client ====================


class TestBriefsListFiltering:
    """BUG-018: Briefs list should only return the requesting client's briefs.

    Previously, returned all briefs to any authenticated user.
    """

    def test_briefs_list_does_not_leak_other_clients_briefs(
        self, api_client, client_user_a, client_a, brief_a, brief_b
    ):
        """Client A's briefs list should NOT include Client B's briefs."""
        headers = _auth_headers(client_user_a, "CLIENT")
        response = api_client.get("/api/v1/briefs", **headers)
        assert response.status_code == 200
        data = json.loads(response.content)
        brief_ids = [b["id"] for b in data]
        assert str(brief_a.id) in brief_ids
        # Should NOT include other client's brief (after fix)
        assert str(brief_b.id) not in brief_ids

    def test_vendor_cannot_see_client_briefs(
        self, api_client, vendor_user_a, vendor_a, brief_a
    ):
        """VENDOR should NOT be able to see CLIENT's briefs via list endpoint."""
        headers = _vendor_headers(vendor_user_a, vendor_a.id)
        response = api_client.get("/api/v1/briefs", **headers)
        assert response.status_code == 200
        data = json.loads(response.content)
        brief_ids = [b["id"] for b in data]
        # Vendor should not see client briefs (after fix)
        assert str(brief_a.id) not in brief_ids


# ==================== BUG-031: Share creation requires ownership ====================


class TestShareCreationOwnership:
    """BUG-031: Share creation should verify the requesting user owns the offer.

    Previously, ownership check short-circuited when vendor_id was None.
    """

    def test_vendor_a_can_create_share_for_own_offer(
        self, api_client, vendor_user_a, vendor_a, offer_a
    ):
        """Vendor A should be able to create a share for their own offer."""
        headers = _vendor_headers(vendor_user_a, vendor_a.id)
        response = api_client.post(
            "/api/v1/shares",
            data=json.dumps({"offerId": str(offer_a.id)}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code in (200, 201)
        data = json.loads(response.content)
        assert "token" in data

    def test_vendor_b_cannot_create_share_for_vendor_a_offer(
        self, api_client, vendor_user_b, vendor_b, offer_a
    ):
        """Vendor B should NOT be able to create a share for Vendor A's offer."""
        headers = _vendor_headers(vendor_user_b, vendor_b.id)
        response = api_client.post(
            "/api/v1/shares",
            data=json.dumps({"offerId": str(offer_a.id)}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 403

    def test_client_cannot_create_share_for_vendor_offer(
        self, api_client, client_user_a, client_a, offer_a
    ):
        """CLIENT should NOT be able to create share links (VENDOR only)."""
        headers = _auth_headers(client_user_a, "CLIENT")
        response = api_client.post(
            "/api/v1/shares",
            data=json.dumps({"offerId": str(offer_a.id)}),
            content_type="application/json",
            **headers,
        )
        # Should be 403 — share creation is VENDOR/SYSTEM only
        assert response.status_code == 403
