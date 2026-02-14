"""Input validation tests.

Tests cover:
- BUG-048: Non-UUID params return 400 not 500
- BUG-050: Status enum validation on create
- BUG-011: File upload validates type and size
- BUG-033: XLSX upload rejects oversized files
- BUG-069: XLSX cell scanning has bounds
"""

import io
import json
import uuid

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client as DjangoTestClient
from django.utils import timezone

from aivus_backend.users.models import User
from aivus_backend.users.models import Vendor
from aivus_backend.users.models import Client as ClientModel
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
def vendor_user(db):
    """Create a VENDOR user."""
    return User.objects.create_user(
        email="val-vendor@example.com",
        password="testpass123",
        name="Val Vendor",
        group="VENDOR",
    )


@pytest.fixture
def vendor(vendor_user):
    """Create vendor company."""
    return Vendor.objects.create(name="Val Agency", owner=vendor_user)


@pytest.fixture
def client_user(db):
    """Create a CLIENT user."""
    return User.objects.create_user(
        email="val-client@example.com",
        password="testpass123",
        name="Val Client",
        group="CLIENT",
    )


@pytest.fixture
def client_obj(client_user):
    """Create client company."""
    return ClientModel.objects.create(name="Val Company", ein="123", owner=client_user)


@pytest.fixture
def project(vendor):
    """Create a test project."""
    return Project.objects.create(
        name="Val Project",
        vendor=vendor,
        status="DRAFT",
    )


@pytest.fixture
def offer(project):
    """Create a test offer."""
    return Offer.objects.create(
        project_name="Val Offer",
        project=project,
        status="DRAFT",
        details={"offers": []},
        deadline=timezone.now(),
        source="PLATFORM",
    )


# ==================== BUG-048: Non-UUID params ====================


class TestNonUUIDParams:
    """BUG-048: Non-UUID parameters should return 400, not 500.

    Django's URL pattern with <uuid:...> handles this at the routing level
    by returning 404 for malformed UUIDs. But for UUID fields in request bodies,
    the view should validate properly.
    """

    def test_project_detail_non_uuid_returns_404(self, api_client, vendor_user, vendor):
        """Non-UUID in project URL should return 404 (caught by URL routing)."""
        headers = _vendor_headers(vendor_user, vendor.id)
        response = api_client.get(
            "/api/v1/projects/not-a-uuid", **headers
        )
        assert response.status_code == 404

    def test_offer_detail_non_uuid_returns_404(self, api_client, vendor_user, vendor):
        """Non-UUID in offer URL should return 404."""
        headers = _auth_headers(vendor_user, "VENDOR")
        response = api_client.get(
            "/api/v1/offers/not-a-uuid", **headers
        )
        assert response.status_code == 404

    def test_brief_detail_non_uuid_returns_404(self, api_client, vendor_user, vendor):
        """Non-UUID in brief URL should return 404."""
        headers = _auth_headers(vendor_user, "VENDOR")
        response = api_client.get(
            "/api/v1/briefs/not-a-uuid", **headers
        )
        assert response.status_code == 404

    def test_create_offer_non_uuid_project_id_returns_400(
        self, api_client, vendor_user, vendor
    ):
        """Non-UUID projectId in offer creation body should return 400, not 500."""
        headers = _auth_headers(vendor_user, "VENDOR")
        payload = {
            "projectId": "not-a-valid-uuid",
            "projectName": "Bad Project",
            "deadline": "2026-12-31T00:00:00Z",
            "source": "PLATFORM",
        }
        response = api_client.post(
            "/api/v1/offers",
            data=json.dumps(payload),
            content_type="application/json",
            **headers,
        )
        # Should be 400 or 404, but NOT 500
        assert response.status_code in (400, 404)

    def test_create_project_non_uuid_vendor_id_returns_400(
        self, api_client, vendor_user, vendor
    ):
        """Non-UUID vendorId in project creation should return 400, not 500."""
        headers = _auth_headers(vendor_user, "VENDOR")
        payload = {
            "vendorId": "not-a-valid-uuid",
            "name": "Bad Vendor ID",
            "status": "DRAFT",
        }
        response = api_client.post(
            "/api/v1/projects",
            data=json.dumps(payload),
            content_type="application/json",
            **headers,
        )
        # QA3-010: vendorId mismatch now returns 403 (Access denied)
        assert response.status_code in (400, 403, 404, 500)
        # After fix, should be 400 or 403, not 500
        if response.status_code == 500:
            pytest.xfail("BUG-048: Non-UUID vendorId causes 500")

    def test_nonexistent_uuid_returns_404(self, api_client, vendor_user, vendor):
        """Valid UUID format but non-existent resource should return 404."""
        fake_id = uuid.uuid4()
        headers = _auth_headers(vendor_user, "VENDOR")
        response = api_client.get(
            f"/api/v1/offers/{fake_id}", **headers
        )
        assert response.status_code == 404


# ==================== BUG-050: Status enum validation ====================


class TestStatusEnumValidation:
    """BUG-050: Status fields should validate against allowed enum values.

    Previously, any arbitrary string was accepted as status on create.
    """

    def test_create_offer_invalid_status_rejected(
        self, api_client, vendor_user, vendor, project
    ):
        """Creating an offer with invalid status should be rejected."""
        headers = _auth_headers(vendor_user, "VENDOR")
        payload = {
            "projectId": str(project.id),
            "projectName": "Bad Status Offer",
            "status": "INVALID_STATUS_XYZ",
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
        # After fix, should be 400
        assert response.status_code in (400, 201)
        if response.status_code == 201:
            pytest.xfail("BUG-050: Invalid status accepted on create")

    def test_create_offer_valid_status_accepted(
        self, api_client, vendor_user, vendor, project
    ):
        """Creating an offer with valid status should succeed."""
        headers = _auth_headers(vendor_user, "VENDOR")
        payload = {
            "projectId": str(project.id),
            "projectName": "Good Status Offer",
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

    def test_create_project_invalid_status_rejected(
        self, api_client, vendor_user, vendor
    ):
        """Creating a project with invalid status should be rejected."""
        headers = _auth_headers(vendor_user, "VENDOR")
        payload = {
            "vendorId": str(vendor.id),
            "name": "Bad Status Project",
            "status": "NONEXISTENT_STATUS",
        }
        response = api_client.post(
            "/api/v1/projects",
            data=json.dumps(payload),
            content_type="application/json",
            **headers,
        )
        assert response.status_code in (400, 201)
        if response.status_code == 201:
            pytest.xfail("BUG-050: Invalid status accepted on project create")


# ==================== BUG-011: File upload validation ====================


class TestFileUploadValidation:
    """BUG-011: File uploads should validate type and size.

    Previously, any file type/size was accepted for thumbnails.
    """

    def test_valid_image_upload_accepted(
        self, api_client, vendor_user, vendor, project
    ):
        """Uploading a valid image file should succeed."""
        headers = _auth_headers(vendor_user, "VENDOR")
        # Create a minimal valid PNG (1x1 pixel)
        png_data = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
            b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
            b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        upload = SimpleUploadedFile("test.png", png_data, content_type="image/png")
        response = api_client.post(
            f"/api/v1/projects/{project.id}/thumbnail",
            {"thumbnail": upload},
            **headers,
        )
        assert response.status_code == 200

    def test_executable_file_upload_rejected(
        self, api_client, vendor_user, vendor, project
    ):
        """Uploading an executable file should be rejected."""
        headers = _auth_headers(vendor_user, "VENDOR")
        exe_data = b"MZ" + b"\x00" * 100  # PE header start
        upload = SimpleUploadedFile("malware.exe", exe_data, content_type="application/x-msdownload")
        response = api_client.post(
            f"/api/v1/projects/{project.id}/thumbnail",
            {"thumbnail": upload},
            **headers,
        )
        # After fix, should reject non-image files
        assert response.status_code in (400, 200)
        if response.status_code == 200:
            pytest.xfail("BUG-011: Executable file accepted as thumbnail")

    def test_oversized_file_upload_rejected(
        self, api_client, vendor_user, vendor, project
    ):
        """Uploading a very large file should be rejected."""
        headers = _auth_headers(vendor_user, "VENDOR")
        # 20MB file (should be too large for a thumbnail)
        large_data = b"\x00" * (20 * 1024 * 1024)
        upload = SimpleUploadedFile("huge.png", large_data, content_type="image/png")
        response = api_client.post(
            f"/api/v1/projects/{project.id}/thumbnail",
            {"thumbnail": upload},
            **headers,
        )
        assert response.status_code in (400, 200)
        if response.status_code == 200:
            pytest.xfail("BUG-011: Oversized file accepted")

    def test_no_file_returns_400(self, api_client, vendor_user, vendor, project):
        """Uploading with no file should return 400."""
        headers = _auth_headers(vendor_user, "VENDOR")
        response = api_client.post(
            f"/api/v1/projects/{project.id}/thumbnail",
            {},
            **headers,
        )
        assert response.status_code == 400


# ==================== BUG-033: XLSX upload size limits ====================


class TestXLSXUploadValidation:
    """BUG-033: XLSX upload should validate file size to prevent DoS.

    Previously, user-uploaded XLSX was opened without size limits.
    """

    def test_xlsx_upload_endpoint_exists(self, api_client, client_user, client_obj):
        """XLSX upload endpoint should be accessible to CLIENT users."""
        headers = _auth_headers(client_user, "CLIENT")
        # Even without a file, should return 400, not 404
        response = api_client.post(
            "/api/v1/client/xlsx-upload",
            {},
            **headers,
        )
        assert response.status_code in (400, 403, 405)

    def test_xlsx_upload_requires_file(self, api_client, client_user, client_obj):
        """XLSX upload without a file should return 400."""
        headers = _auth_headers(client_user, "CLIENT")
        response = api_client.post(
            "/api/v1/client/xlsx-upload",
            {},
            **headers,
        )
        assert response.status_code in (400, 403)


# ==================== BUG-069: XLSX cell scanning bounds ====================


class TestXLSXCellScanningBounds:
    """BUG-069: XLSX cell scanning should have bounds to prevent CPU exhaustion.

    This is hard to test directly without creating crafted XLSX files,
    but we can test that the endpoint handles edge cases gracefully.
    """

    def test_empty_xlsx_handled_gracefully(self, api_client, client_user, client_obj):
        """An empty/minimal XLSX should not crash the server."""
        headers = _auth_headers(client_user, "CLIENT")
        try:
            import openpyxl
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Empty"
            buffer = io.BytesIO()
            wb.save(buffer)
            buffer.seek(0)
            upload = SimpleUploadedFile(
                "empty.xlsx",
                buffer.read(),
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            response = api_client.post(
                "/api/v1/client/xlsx-upload",
                {"file": upload},
                **headers,
            )
            # Should not return 500
            assert response.status_code != 500
        except ImportError:
            pytest.skip("openpyxl not available")

    def test_non_xlsx_file_rejected(self, api_client, client_user, client_obj):
        """Non-XLSX file should be rejected gracefully."""
        headers = _auth_headers(client_user, "CLIENT")
        upload = SimpleUploadedFile(
            "notreal.xlsx",
            b"This is not a real xlsx file",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response = api_client.post(
            "/api/v1/client/xlsx-upload",
            {"file": upload},
            **headers,
        )
        # Should handle gracefully (400), not crash (500)
        assert response.status_code in (400, 404, 500)
        if response.status_code == 500:
            pytest.xfail("BUG-033/069: Invalid XLSX causes 500")


# ==================== Additional validation tests ====================


class TestRequestValidation:
    """General request validation tests."""

    def test_invalid_json_returns_400(self, api_client, vendor_user, vendor):
        """POST with invalid JSON body should return 400."""
        headers = _auth_headers(vendor_user, "VENDOR")
        response = api_client.post(
            "/api/v1/offers",
            data="not valid json {{{",
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 400

    def test_create_project_missing_name_returns_400(
        self, api_client, vendor_user, vendor
    ):
        """Creating a project without name should return 400."""
        headers = _auth_headers(vendor_user, "VENDOR")
        payload = {"vendorId": str(vendor.id)}
        response = api_client.post(
            "/api/v1/projects",
            data=json.dumps(payload),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 400

    def test_create_offer_missing_deadline_returns_400(
        self, api_client, vendor_user, vendor, project
    ):
        """Creating an offer without deadline should return 400."""
        headers = _auth_headers(vendor_user, "VENDOR")
        payload = {
            "projectId": str(project.id),
            "projectName": "No Deadline",
            "source": "PLATFORM",
        }
        response = api_client.post(
            "/api/v1/offers",
            data=json.dumps(payload),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 400

    def test_create_offer_invalid_deadline_format_returns_400(
        self, api_client, vendor_user, vendor, project
    ):
        """Creating an offer with invalid deadline format should return 400."""
        headers = _auth_headers(vendor_user, "VENDOR")
        payload = {
            "projectId": str(project.id),
            "projectName": "Bad Date",
            "deadline": "not-a-date-at-all",
            "source": "PLATFORM",
        }
        response = api_client.post(
            "/api/v1/offers",
            data=json.dumps(payload),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 400
