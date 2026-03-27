"""Integration tests for Projects API endpoints.

Tests cover:
1. CRUD operations for Project, Offer, Brief
2. Authentication/authorization checks
3. Request validation
4. Soft delete behavior
5. Collaborators and Client Managers
"""

import json
import uuid

import pytest
from django.conf import settings
from django.test import Client as DjangoTestClient
from django.utils import timezone

from aivus_backend.catalog.models import Category
from aivus_backend.catalog.models import Entry
from aivus_backend.projects.models import Brief
from aivus_backend.projects.models import Offer
from aivus_backend.projects.models import Project
from aivus_backend.users.models import Client as ClientModel
from aivus_backend.users.models import Team
from aivus_backend.users.models import User
from aivus_backend.users.models import Vendor


@pytest.fixture
def api_client():
    """Create a Django test client."""
    return DjangoTestClient()


@pytest.fixture
def vendor_user(db):
    """Create a vendor user."""
    return User.objects.create_user(
        email="api-vendor@example.com",
        password="testpass123",
        name="API Test Vendor",
        group="VENDOR",
    )


@pytest.fixture
def client_user(db):
    """Create a client user."""
    return User.objects.create_user(
        email="api-client@example.com",
        password="testpass123",
        name="API Test Client",
        group="CLIENT",
    )


@pytest.fixture
def vendor(vendor_user):
    """Create a vendor."""
    return Vendor.objects.create(name="API Test Agency", owner=vendor_user)


@pytest.fixture
def team(db):
    """Create a team."""
    return Team.objects.create(name="Test Team")


@pytest.fixture
def category(db):
    """Create a category."""
    return Category.objects.create(name="Test Category", level=1)


@pytest.fixture
def entry(category):
    """Create an entry."""
    return Entry.objects.create(
        name="Test Entry",
        category=category,
        is_approved=True,
    )


@pytest.fixture
def project(vendor):
    """Create a test project."""
    return Project.objects.create(
        name="Existing Project",
        vendor=vendor,
        status="DRAFT",
    )


@pytest.fixture
def client_entity(client_user):
    """Create a client entity."""
    return ClientModel.objects.create(name="API Test Company", owner=client_user)


@pytest.fixture
def brief(client_entity):
    """Create a test brief linked to a client."""
    return Brief.objects.create(
        status="DRAFT",
        details={"projectName": "Test Brief"},
        client=client_entity,
    )


@pytest.fixture
def offer(project):
    """Create a test offer."""
    return Offer.objects.create(
        project_name="Existing Offer",
        project=project,
        status="DRAFT",
        details={"offers": []},
        deadline=timezone.now(),
        source="PLATFORM",
    )


def _auth_headers(user, group=None):
    """Build authentication headers for API requests.

    Uses the API Key from Django settings so tests work
    regardless of environment (local, CI, etc.).
    """
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


# ==================== Auth Tests ====================


class TestAuthentication:
    """Test authentication requirements."""

    def test_projects_list_requires_auth(self, api_client):
        """Unauthenticated request to projects should fail."""
        response = api_client.get("/api/v1/projects")
        assert response.status_code in (401, 403)

    def test_offers_list_requires_auth(self, api_client):
        """Unauthenticated request to offers should fail."""
        response = api_client.get("/api/v1/offers")
        assert response.status_code in (401, 403)

    def test_briefs_list_requires_auth(self, api_client):
        """Unauthenticated request to briefs should fail."""
        response = api_client.get("/api/v1/briefs")
        assert response.status_code in (401, 403)

    def test_wrong_group_access_denied(self, api_client, db):
        """Request with non-allowed group should be denied (403)."""
        # Create a user with UNCONFIRMED group (not allowed for projects)
        unconfirmed_user = User.objects.create_user(
            email="unconfirmed@example.com",
            password="testpass123",
            name="Unconfirmed User",
            group="UNCONFIRMED",
        )
        headers = _auth_headers(unconfirmed_user, "UNCONFIRMED")
        response = api_client.get("/api/v1/projects", **headers)
        # require_groups decorator returns 403 for wrong group
        assert response.status_code == 403
        data = json.loads(response.content)
        assert "Access denied" in data["error"]


# ==================== Projects API Tests ====================


class TestProjectsAPI:
    """Test Projects CRUD API."""

    def test_projects_list_requires_vendor_id(self, api_client, client_user):
        """GET /projects without vendor context should return 400."""
        # QA3-009: vendor_id now comes from user_data, not header
        # A CLIENT user has no vendor_id, so they get 400
        headers = _auth_headers(client_user, "CLIENT")
        response = api_client.get("/api/v1/projects", **headers)
        assert response.status_code == 400
        data = json.loads(response.content)
        assert "Vendor ID required" in data["error"]

    def test_projects_list_with_vendor_id(
        self, api_client, vendor_user, vendor, project
    ):
        """GET /projects with X-Vendor-Id should return projects."""
        headers = _vendor_headers(vendor_user, vendor.id)
        response = api_client.get("/api/v1/projects", **headers)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[0]["name"] == "Existing Project"

    def test_create_project(self, api_client, vendor_user, vendor):
        """POST /projects should create a new project."""
        headers = _auth_headers(vendor_user, "VENDOR")
        payload = {
            "vendorId": str(vendor.id),
            "name": "New Project",
            "status": "DRAFT",
        }
        response = api_client.post(
            "/api/v1/projects",
            data=json.dumps(payload),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 201
        data = json.loads(response.content)
        assert data["name"] == "New Project"
        assert data["vendorId"] == str(vendor.id)
        assert data["status"] == "DRAFT"

    def test_create_project_missing_fields(self, api_client, vendor_user, vendor):
        """POST /projects with missing required fields should return 400."""
        headers = _auth_headers(vendor_user, "VENDOR")
        payload = {"name": "No Vendor"}  # missing vendorId
        response = api_client.post(
            "/api/v1/projects",
            data=json.dumps(payload),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 400

    def test_get_project_detail(self, api_client, vendor_user, vendor, project):
        """GET /projects/<id> should return project details."""
        headers = _auth_headers(vendor_user, "VENDOR")
        response = api_client.get(f"/api/v1/projects/{project.id}", **headers)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["id"] == str(project.id)
        assert data["name"] == "Existing Project"
        assert "collaborators" in data
        assert "clientManagers" in data

    def test_update_project(self, api_client, vendor_user, vendor, project):
        """PATCH /projects/<id> should update project."""
        headers = _auth_headers(vendor_user, "VENDOR")
        payload = {"name": "Updated Project Name", "description": "New desc"}
        response = api_client.patch(
            f"/api/v1/projects/{project.id}",
            data=json.dumps(payload),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["name"] == "Updated Project Name"
        assert data["description"] == "New desc"

    def test_delete_project(self, api_client, vendor_user, vendor, project):
        """DELETE /projects/<id> should soft delete."""
        headers = _auth_headers(vendor_user, "VENDOR")
        response = api_client.delete(f"/api/v1/projects/{project.id}", **headers)
        assert response.status_code == 200
        # Verify soft delete
        project.refresh_from_db()
        assert project.deleted_at is not None

    def test_get_deleted_project_returns_404(
        self, api_client, vendor_user, vendor, project
    ):
        """GET on soft-deleted project should return 404."""
        headers = _auth_headers(vendor_user, "VENDOR")
        project.deleted_at = timezone.now()
        project.save()
        response = api_client.get(f"/api/v1/projects/{project.id}", **headers)
        assert response.status_code == 404

    def test_create_project_with_collaborators(self, api_client, vendor_user, vendor):
        """POST /projects with collaborators should create them."""
        headers = _auth_headers(vendor_user, "VENDOR")
        payload = {
            "vendorId": str(vendor.id),
            "name": "Project With Collabs",
            "status": "DRAFT",
            "collaborators": [
                {
                    "name": "John Doe",
                    "email": "john@example.com",
                    "role": "internal_user",
                },
                {
                    "name": "Jane Smith",
                    "email": "jane@example.com",
                    "role": "external_user",
                },
            ],
        }
        response = api_client.post(
            "/api/v1/projects",
            data=json.dumps(payload),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 201
        data = json.loads(response.content)
        assert len(data["collaborators"]) == 2

    def test_create_project_with_client_managers(self, api_client, vendor_user, vendor):
        """POST /projects with client managers should create them."""
        headers = _auth_headers(vendor_user, "VENDOR")
        payload = {
            "vendorId": str(vendor.id),
            "name": "Project With Managers",
            "status": "DRAFT",
            "clientManagers": [
                {"name": "Manager One", "position": "VP Marketing"},
                {"name": "Manager Two", "position": "Director"},
            ],
        }
        response = api_client.post(
            "/api/v1/projects",
            data=json.dumps(payload),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 201
        data = json.loads(response.content)
        assert len(data["clientManagers"]) == 2

    def test_get_nonexistent_project(self, api_client, vendor_user, vendor):
        """GET /projects/<random-uuid> should return 404."""
        headers = _auth_headers(vendor_user, "VENDOR")
        fake_id = uuid.uuid4()
        response = api_client.get(f"/api/v1/projects/{fake_id}", **headers)
        assert response.status_code == 404


# ==================== Offers API Tests ====================


class TestOffersAPI:
    """Test Offers CRUD API."""

    def test_create_offer(self, api_client, vendor_user, vendor, project):
        """POST /offers should create a new offer."""
        headers = _auth_headers(vendor_user, "VENDOR")
        payload = {
            "projectId": str(project.id),
            "projectName": "New Offer",
            "status": "DRAFT",
            "deadline": "2026-12-31T00:00:00Z",
            "source": "PLATFORM",
            "details": {"offers": []},
        }
        response = api_client.post(
            "/api/v1/offers",
            data=json.dumps(payload),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 201
        data = json.loads(response.content)
        assert data["projectName"] == "New Offer"
        assert data["projectId"] == str(project.id)

    def test_create_offer_missing_fields(self, api_client, vendor_user, vendor):
        """POST /offers without required fields should return 400."""
        headers = _auth_headers(vendor_user, "VENDOR")
        payload = {"projectName": "Incomplete"}
        response = api_client.post(
            "/api/v1/offers",
            data=json.dumps(payload),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 400

    def test_get_offer_detail(self, api_client, vendor_user, vendor, offer):
        """GET /offers/<id> should return offer details."""
        headers = _auth_headers(vendor_user, "VENDOR")
        response = api_client.get(f"/api/v1/offers/{offer.id}", **headers)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["id"] == str(offer.id)
        assert data["projectName"] == "Existing Offer"
        assert "details" in data

    def test_update_offer(self, api_client, vendor_user, vendor, offer):
        """PATCH /offers/<id> should update offer."""
        headers = _auth_headers(vendor_user, "VENDOR")
        payload = {"projectName": "Updated Offer", "cost": 5000}
        response = api_client.patch(
            f"/api/v1/offers/{offer.id}",
            data=json.dumps(payload),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["projectName"] == "Updated Offer"
        assert data["cost"] == 5000

    def test_delete_offer(self, api_client, vendor_user, vendor, offer):
        """DELETE /offers/<id> should soft delete."""
        headers = _auth_headers(vendor_user, "VENDOR")
        response = api_client.delete(f"/api/v1/offers/{offer.id}", **headers)
        assert response.status_code == 200
        offer.refresh_from_db()
        assert offer.deleted_at is not None

    def test_offers_by_project(self, api_client, vendor_user, vendor, project, offer):
        """GET /offers/project/<id> should return offers for project."""
        headers = _auth_headers(vendor_user, "VENDOR")
        response = api_client.get(f"/api/v1/offers/project/{project.id}", **headers)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_offers_list_filtered(
        self, api_client, vendor_user, vendor, project, offer
    ):
        """GET /offers?projectId=<id> should filter offers."""
        headers = _auth_headers(vendor_user, "VENDOR")
        response = api_client.get(f"/api/v1/offers?projectId={project.id}", **headers)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert all(o["projectId"] == str(project.id) for o in data)

    def test_create_offer_invalid_deadline(
        self, api_client, vendor_user, vendor, project
    ):
        """POST /offers with invalid deadline should return 400."""
        headers = _auth_headers(vendor_user, "VENDOR")
        payload = {
            "projectId": str(project.id),
            "projectName": "Bad Deadline",
            "deadline": "not-a-date",
            "source": "PLATFORM",
        }
        response = api_client.post(
            "/api/v1/offers",
            data=json.dumps(payload),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 400


# ==================== Briefs API Tests ====================


class TestBriefsAPI:
    """Test Briefs CRUD API."""

    def test_create_brief(self, api_client, client_user, client_entity):
        """POST /briefs should create a new brief."""
        headers = _auth_headers(client_user, "CLIENT")
        payload = {
            "status": "DRAFT",
            "details": {
                "projectName": "New Brief",
                "budget": 10000,
            },
        }
        response = api_client.post(
            "/api/v1/briefs",
            data=json.dumps(payload),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 201
        data = json.loads(response.content)
        assert data["status"] == "DRAFT"
        assert data["details"]["projectName"] == "New Brief"

    def test_get_brief_detail(self, api_client, client_user, client_entity, brief):
        """GET /briefs/<id> should return brief details (requires client ownership)."""
        headers = _auth_headers(client_user, "CLIENT")
        response = api_client.get(f"/api/v1/briefs/{brief.id}", **headers)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["id"] == str(brief.id)

    def test_update_brief(self, api_client, client_user, client_entity, brief):
        """PATCH /briefs/<id> should update brief (requires client ownership)."""
        headers = _auth_headers(client_user, "CLIENT")
        payload = {
            "status": "SUBMITTED",
            "details": {"projectName": "Updated Brief", "budget": 20000},
        }
        response = api_client.patch(
            f"/api/v1/briefs/{brief.id}",
            data=json.dumps(payload),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["status"] == "SUBMITTED"
        assert data["details"]["budget"] == 20000

    def test_delete_brief(self, api_client, client_user, client_entity, brief):
        """DELETE /briefs/<id> should soft delete (requires client ownership)."""
        headers = _auth_headers(client_user, "CLIENT")
        response = api_client.delete(f"/api/v1/briefs/{brief.id}", **headers)
        assert response.status_code == 200
        brief.refresh_from_db()
        assert brief.deleted_at is not None

    def test_briefs_list(self, api_client, client_user, client_entity, brief):
        """GET /briefs should return client's briefs."""
        headers = _auth_headers(client_user, "CLIENT")
        response = api_client.get("/api/v1/briefs", **headers)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_create_brief_invalid_json(self, api_client, client_user, client_entity):
        """POST /briefs with invalid JSON should return 400."""
        headers = _auth_headers(client_user, "CLIENT")
        response = api_client.post(
            "/api/v1/briefs",
            data="not json at all",
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 400
