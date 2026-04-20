"""Extended API tests: shares, templates, rates, copy, status, xlsx, export."""

import io
import json
import uuid

import openpyxl
import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client as DjangoTestClient
from django.utils import timezone

from aivus_backend.catalog.models import Category
from aivus_backend.catalog.models import Entry
from aivus_backend.catalog.models import Unit
from aivus_backend.projects.models import Brief
from aivus_backend.projects.models import BriefOffer
from aivus_backend.projects.models import Offer
from aivus_backend.projects.models import OfferDeliverable
from aivus_backend.projects.models import OfferEntry
from aivus_backend.projects.models import OfferScheduleEntry
from aivus_backend.projects.models import Project
from aivus_backend.projects.models import RateCard
from aivus_backend.projects.models import RateCardItem
from aivus_backend.projects.models import Share
from aivus_backend.projects.models import Template
from aivus_backend.users.models import Client as ClientModel
from aivus_backend.users.models import User
from aivus_backend.users.models import Vendor


@pytest.fixture
def api():
    return DjangoTestClient()


@pytest.fixture
def vendor_user(db):
    return User.objects.create_user(
        email="ext-vendor@example.com",
        password="testpass123",
        name="Ext Vendor",
        group="VENDOR",
    )


@pytest.fixture
def client_user(db):
    return User.objects.create_user(
        email="ext-client@example.com",
        password="testpass123",
        name="Ext Client",
        group="CLIENT",
    )


@pytest.fixture
def vendor(vendor_user):
    return Vendor.objects.create(name="Ext Agency", owner=vendor_user)


@pytest.fixture
def client_entity(client_user):
    return ClientModel.objects.create(name="Ext Company", owner=client_user)


@pytest.fixture
def category(db):
    return Category.objects.create(name="Test Cat", level=1, tags=["production"])


@pytest.fixture
def entry(category):
    return Entry.objects.create(name="Test Entry", category=category, is_approved=True)


@pytest.fixture
def unit(db):
    return Unit.objects.create(name="Day", symbol="Days", dimension="TEMPORAL")


@pytest.fixture
def project(vendor):
    return Project.objects.create(name="Ext Project", vendor=vendor, status="DRAFT")


@pytest.fixture
def offer(project):
    return Offer.objects.create(
        project_name="Ext Offer",
        project=project,
        status="DRAFT",
        details={"offers": []},
        deadline=timezone.now(),
        source="PLATFORM",
    )


@pytest.fixture
def published_offer(project):
    return Offer.objects.create(
        project_name="Published Offer",
        project=project,
        status="PUBLISHED",
        details={"offers": [{"id": "r1", "price": 100, "cost": 100}]},
        deadline=timezone.now(),
        source="PLATFORM",
    )


@pytest.fixture
def brief(client_entity):
    return Brief.objects.create(
        status="DRAFT",
        details={"projectName": "Ext Brief"},
        client=client_entity,
    )


@pytest.fixture
def share(published_offer, vendor_user):
    return Share.objects.create(
        offer=published_offer,
        created_by=vendor_user,
    )


def _auth(user, group=None):
    return {
        "HTTP_X_API_KEY": settings.API_KEY,
        "HTTP_X_USER_ID": str(user.id),
        "HTTP_X_USER_GROUP": group or user.group,
    }


def _vendor_auth(user, vendor_id):
    headers = _auth(user, "VENDOR")
    headers["HTTP_X_VENDOR_ID"] = str(vendor_id)
    return headers


def _client_auth(user, client_id):
    headers = _auth(user, "CLIENT")
    headers["HTTP_X_CLIENT_ID"] = str(client_id)
    return headers


class TestSharesAPI:
    def test_create_share(self, api, vendor_user, vendor, offer):
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.post(
            "/api/v1/shares",
            data=json.dumps({"offerId": str(offer.id)}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 201
        data = json.loads(response.content)
        assert "token" in data
        assert data["isActive"] is True

    def test_create_share_auto_publishes_draft(self, api, vendor_user, vendor, offer):
        headers = _vendor_auth(vendor_user, vendor.id)
        assert offer.status == "DRAFT"
        api.post(
            "/api/v1/shares",
            data=json.dumps({"offerId": str(offer.id)}),
            content_type="application/json",
            **headers,
        )
        offer.refresh_from_db()
        assert offer.status == "PUBLISHED"

    def test_create_share_reuses_existing(
        self, api, vendor_user, vendor, published_offer, share
    ):
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.post(
            "/api/v1/shares",
            data=json.dumps({"offerId": str(published_offer.id)}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["token"] == share.token

    def test_create_share_missing_offer_id(self, api, vendor_user, vendor):
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.post(
            "/api/v1/shares",
            data=json.dumps({}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 400

    def test_create_share_nonexistent_offer(self, api, vendor_user, vendor):
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.post(
            "/api/v1/shares",
            data=json.dumps({"offerId": str(uuid.uuid4())}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 404

    def test_get_share_by_offer_id(
        self, api, vendor_user, vendor, published_offer, share
    ):
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.get(
            f"/api/v1/shares?offerId={published_offer.id}",
            **headers,
        )
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["token"] == share.token

    def test_get_share_by_offer_id_missing_param(self, api, vendor_user, vendor):
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.get("/api/v1/shares", **headers)
        assert response.status_code == 400

    def test_share_get_public(self, api, share):
        response = api.get(f"/api/v1/shares/{share.token}")
        assert response.status_code == 200
        data = json.loads(response.content)
        assert "offer" in data

    def test_share_get_public_not_found(self, api, db):
        response = api.get("/api/v1/shares/nonexistent-token")
        assert response.status_code == 404

    def test_share_get_public_inactive(self, api, share):
        share.is_active = False
        share.save()
        response = api.get(f"/api/v1/shares/{share.token}")
        assert response.status_code == 410

    def test_share_get_public_archived_project(self, api, share):
        share.offer.project.deleted_at = timezone.now()
        share.offer.project.save()
        response = api.get(f"/api/v1/shares/{share.token}")
        assert response.status_code == 410

    def test_share_get_public_draft_offer_blocked(self, api, project, vendor_user):
        draft_offer = Offer.objects.create(
            project_name="Draft",
            project=project,
            status="DRAFT",
            details={},
            deadline=timezone.now(),
            source="PLATFORM",
        )
        s = Share.objects.create(offer=draft_offer, created_by=vendor_user)
        response = api.get(f"/api/v1/shares/{s.token}")
        assert response.status_code == 404

    def test_share_manage_toggle(self, api, vendor_user, vendor, share):
        headers = _vendor_auth(vendor_user, vendor.id)
        assert share.is_active is True
        response = api.patch(
            f"/api/v1/shares/{share.token}/manage",
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 200
        share.refresh_from_db()
        assert share.is_active is False

    def test_share_manage_set_active(self, api, vendor_user, vendor, share):
        share.is_active = False
        share.save()
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.patch(
            f"/api/v1/shares/{share.token}/manage",
            data=json.dumps({"isActive": True}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 200
        share.refresh_from_db()
        assert share.is_active is True

    def test_share_manage_delete_deactivates(self, api, vendor_user, vendor, share):
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.delete(
            f"/api/v1/shares/{share.token}/manage",
            **headers,
        )
        assert response.status_code == 200
        share.refresh_from_db()
        assert share.is_active is False

    def test_share_manage_wrong_vendor(self, api, db):
        other_user = User.objects.create_user(
            email="other@example.com", password="pass", name="Other", group="VENDOR"
        )
        other_vendor = Vendor.objects.create(name="Other Agency", owner=other_user)
        first_user = User.objects.create_user(
            email="first@example.com", password="pass", name="First", group="VENDOR"
        )
        first_vendor = Vendor.objects.create(name="First Agency", owner=first_user)
        proj = Project.objects.create(name="P", vendor=first_vendor, status="DRAFT")
        off = Offer.objects.create(
            project_name="O",
            project=proj,
            status="PUBLISHED",
            details={},
            deadline=timezone.now(),
            source="PLATFORM",
        )
        s = Share.objects.create(offer=off, created_by=first_user)
        headers = _vendor_auth(other_user, other_vendor.id)
        response = api.patch(
            f"/api/v1/shares/{s.token}/manage",
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 403


class TestShareLinkToBrief:
    def test_link_share_to_brief(self, api, client_user, client_entity, share, brief):
        headers = _client_auth(client_user, client_entity.id)
        response = api.post(
            f"/api/v1/shares/{share.token}/link",
            data=json.dumps({"briefId": str(brief.id)}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 201
        assert BriefOffer.objects.filter(brief=brief, offer=share.offer).exists()

    def test_link_share_duplicate_returns_existing(
        self, api, client_user, client_entity, share, brief
    ):
        headers = _client_auth(client_user, client_entity.id)
        BriefOffer.objects.create(brief=brief, offer=share.offer, linked_by=client_user)
        response = api.post(
            f"/api/v1/shares/{share.token}/link",
            data=json.dumps({"briefId": str(brief.id)}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 200

    def test_link_share_missing_brief_id(self, api, client_user, client_entity, share):
        headers = _client_auth(client_user, client_entity.id)
        response = api.post(
            f"/api/v1/shares/{share.token}/link",
            data=json.dumps({}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 400

    def test_link_share_inactive_share(
        self, api, client_user, client_entity, share, brief
    ):
        share.is_active = False
        share.save()
        headers = _client_auth(client_user, client_entity.id)
        response = api.post(
            f"/api/v1/shares/{share.token}/link",
            data=json.dumps({"briefId": str(brief.id)}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 410

    def test_link_share_wrong_client(self, api, db, share):
        other_user = User.objects.create_user(
            email="other-c@example.com", password="pass", name="OtherC", group="CLIENT"
        )
        other_client = ClientModel.objects.create(name="OtherCo", owner=other_user)
        Brief.objects.create(status="DRAFT", details={}, client=other_client)
        first_user = User.objects.create_user(
            email="first-c@example.com", password="pass", name="FirstC", group="CLIENT"
        )
        first_client = ClientModel.objects.create(name="FirstCo", owner=first_user)
        first_brief = Brief.objects.create(
            status="DRAFT", details={}, client=first_client
        )
        headers2 = _client_auth(other_user, other_client.id)
        response = api.post(
            f"/api/v1/shares/{share.token}/link",
            data=json.dumps({"briefId": str(first_brief.id)}),
            content_type="application/json",
            **headers2,
        )
        assert response.status_code == 403


class TestOfferStatusAPI:
    def test_update_status_to_published(self, api, vendor_user, vendor, offer):
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.patch(
            f"/api/v1/offers/{offer.id}/status",
            data=json.dumps({"status": "PUBLISHED"}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["status"] == "PUBLISHED"

    def test_update_status_to_archived(self, api, vendor_user, vendor, offer):
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.patch(
            f"/api/v1/offers/{offer.id}/status",
            data=json.dumps({"status": "ARCHIVED"}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["status"] == "ARCHIVED"

    def test_update_status_invalid(self, api, vendor_user, vendor, offer):
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.patch(
            f"/api/v1/offers/{offer.id}/status",
            data=json.dumps({"status": "INVALID"}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 400

    def test_update_status_missing(self, api, vendor_user, vendor, offer):
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.patch(
            f"/api/v1/offers/{offer.id}/status",
            data=json.dumps({}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 400

    def test_update_status_nonexistent_offer(self, api, vendor_user, vendor):
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.patch(
            f"/api/v1/offers/{uuid.uuid4()}/status",
            data=json.dumps({"status": "PUBLISHED"}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 404

    def test_update_status_wrong_vendor(self, api, db, offer):
        other_user = User.objects.create_user(
            email="other-v@example.com", password="pass", name="OtherV", group="VENDOR"
        )
        other_vendor = Vendor.objects.create(name="Other Vendor", owner=other_user)
        headers = _vendor_auth(other_user, other_vendor.id)
        response = api.patch(
            f"/api/v1/offers/{offer.id}/status",
            data=json.dumps({"status": "PUBLISHED"}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 403


class TestOfferCopyAPI:
    def test_copy_offer(self, api, vendor_user, vendor, offer):
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.post(
            f"/api/v1/offers/{offer.id}/copy",
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 201
        data = json.loads(response.content)
        assert data["projectName"] == "Ext Offer (Copy)"
        assert data["status"] == "DRAFT"
        assert data["id"] != str(offer.id)

    def test_copy_offer_with_entries(
        self, api, vendor_user, vendor, offer, category, entry
    ):
        OfferEntry.objects.create(
            offer=offer,
            item_name="Entry 1",
            entry=entry,
            category=category,
            price=100,
            cost=200,
            sort_order=0,
        )
        OfferEntry.objects.create(
            offer=offer,
            item_name="Entry 2",
            entry=entry,
            category=category,
            price=300,
            cost=600,
            sort_order=1,
        )
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.post(
            f"/api/v1/offers/{offer.id}/copy",
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 201
        data = json.loads(response.content)
        new_entries = OfferEntry.objects.filter(offer_id=data["id"])
        assert new_entries.count() == 2

    def test_copy_offer_with_deliverables(self, api, vendor_user, vendor, offer):
        OfferDeliverable.objects.create(
            offer=offer,
            quantity=2,
            duration="30",
            duration_unit="sec",
            notes="Test deliverable",
            sort_order=0,
        )
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.post(
            f"/api/v1/offers/{offer.id}/copy",
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 201
        data = json.loads(response.content)
        new_deliverables = OfferDeliverable.objects.filter(
            offer_id=data["id"], deleted_at__isnull=True
        )
        assert new_deliverables.count() == 1
        first_deliverable = new_deliverables.first()
        assert first_deliverable is not None
        assert first_deliverable.notes == "Test deliverable"

    def test_copy_offer_with_schedule_entries(self, api, vendor_user, vendor, offer):
        OfferScheduleEntry.objects.create(
            offer=offer,
            phase_type="prep",
            days=5,
            hours_per_day=8,
            notes="Prep phase",
            sort_order=0,
        )
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.post(
            f"/api/v1/offers/{offer.id}/copy",
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 201
        data = json.loads(response.content)
        new_schedules = OfferScheduleEntry.objects.filter(
            offer_id=data["id"], deleted_at__isnull=True
        )
        assert new_schedules.count() == 1

    def test_copy_nonexistent_offer(self, api, vendor_user, vendor):
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.post(
            f"/api/v1/offers/{uuid.uuid4()}/copy",
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 404

    def test_copy_offer_wrong_vendor(self, api, db, offer):
        other_user = User.objects.create_user(
            email="copy-other@example.com",
            password="pass",
            name="CopyOther",
            group="VENDOR",
        )
        other_vendor = Vendor.objects.create(name="Copy Agency", owner=other_user)
        headers = _vendor_auth(other_user, other_vendor.id)
        response = api.post(
            f"/api/v1/offers/{offer.id}/copy",
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 403


class TestTemplatesAPI:
    def test_list_templates_empty(self, api, vendor_user, vendor):
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.get("/api/v1/templates", **headers)
        assert response.status_code == 200
        assert json.loads(response.content) == []

    def test_create_template_from_offer(self, api, vendor_user, vendor, offer):
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.post(
            "/api/v1/templates",
            data=json.dumps(
                {
                    "offerId": str(offer.id),
                    "name": "My Template",
                }
            ),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 201
        data = json.loads(response.content)
        assert data["name"] == "My Template"

    def test_create_template_missing_fields(self, api, vendor_user, vendor):
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.post(
            "/api/v1/templates",
            data=json.dumps({"name": "No Offer"}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 400

    def test_create_template_nonexistent_offer(self, api, vendor_user, vendor):
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.post(
            "/api/v1/templates",
            data=json.dumps(
                {
                    "offerId": str(uuid.uuid4()),
                    "name": "Bad Template",
                }
            ),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 404

    def test_list_templates_returns_created(self, api, vendor_user, vendor, offer):
        Template.objects.create(
            name="T1",
            vendor=vendor,
            details={"offers": []},
            metadata={"status": "DRAFT"},
        )
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.get("/api/v1/templates", **headers)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert len(data) == 1
        assert data[0]["name"] == "T1"

    def test_get_template_detail(self, api, vendor_user, vendor):
        template = Template.objects.create(
            name="T2",
            vendor=vendor,
            details={"offers": []},
            metadata={"status": "DRAFT"},
        )
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.get(f"/api/v1/templates/{template.id}", **headers)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["name"] == "T2"

    def test_update_template(self, api, vendor_user, vendor):
        template = Template.objects.create(
            name="Old",
            vendor=vendor,
            details={},
            metadata={},
        )
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.patch(
            f"/api/v1/templates/{template.id}",
            data=json.dumps({"name": "New Name"}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["name"] == "New Name"

    def test_delete_template(self, api, vendor_user, vendor):
        template = Template.objects.create(
            name="ToDelete",
            vendor=vendor,
            details={},
            metadata={},
        )
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.delete(f"/api/v1/templates/{template.id}", **headers)
        assert response.status_code == 200
        template.refresh_from_db()
        assert template.deleted_at is not None

    def test_get_template_wrong_vendor(self, api, db, vendor):
        template = Template.objects.create(
            name="T3",
            vendor=vendor,
            details={},
            metadata={},
        )
        other_user = User.objects.create_user(
            email="tmpl-other@example.com",
            password="pass",
            name="TmplOther",
            group="VENDOR",
        )
        other_vendor = Vendor.objects.create(name="Tmpl Agency", owner=other_user)
        headers = _vendor_auth(other_user, other_vendor.id)
        response = api.get(f"/api/v1/templates/{template.id}", **headers)
        assert response.status_code == 404


class TestRateCardsAPI:
    def test_list_rate_cards_empty(self, api, vendor_user, vendor):
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.get("/api/v1/rates", **headers)
        assert response.status_code == 200
        assert json.loads(response.content) == []

    def test_create_rate_card(self, api, vendor_user, vendor):
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.post(
            "/api/v1/rates",
            data=json.dumps(
                {
                    "name": "NYC Rates 2026",
                    "items": [],
                }
            ),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 201
        data = json.loads(response.content)
        assert data["name"] == "NYC Rates 2026"

    def test_create_rate_card_with_items(self, api, vendor_user, vendor, entry, unit):
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.post(
            "/api/v1/rates",
            data=json.dumps(
                {
                    "name": "Detailed Rates",
                    "items": [
                        {
                            "entryId": str(entry.id),
                            "unitId": str(unit.id),
                            "price": 500,
                        },
                    ],
                }
            ),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 201
        data = json.loads(response.content)
        assert len(data["items"]) == 1
        assert float(data["items"][0]["price"]) == 500

    def test_create_rate_card_missing_name(self, api, vendor_user, vendor):
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.post(
            "/api/v1/rates",
            data=json.dumps({"items": []}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 400

    def test_get_rate_card_detail(self, api, vendor_user, vendor):
        rc = RateCard.objects.create(name="RC1", vendor=vendor)
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.get(f"/api/v1/rates/{rc.id}", **headers)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["name"] == "RC1"

    def test_update_rate_card(self, api, vendor_user, vendor):
        rc = RateCard.objects.create(name="OldRC", vendor=vendor)
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.patch(
            f"/api/v1/rates/{rc.id}",
            data=json.dumps({"name": "Updated RC"}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["name"] == "Updated RC"

    def test_delete_rate_card(self, api, vendor_user, vendor):
        rc = RateCard.objects.create(name="DeleteRC", vendor=vendor)
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.delete(f"/api/v1/rates/{rc.id}", **headers)
        assert response.status_code == 200
        rc.refresh_from_db()
        assert rc.deleted_at is not None

    def test_get_rate_card_wrong_vendor(self, api, db, vendor):
        rc = RateCard.objects.create(name="RC2", vendor=vendor)
        other_user = User.objects.create_user(
            email="rc-other@example.com",
            password="pass",
            name="RCOther",
            group="VENDOR",
        )
        other_vendor = Vendor.objects.create(name="RC Agency", owner=other_user)
        headers = _vendor_auth(other_user, other_vendor.id)
        response = api.get(f"/api/v1/rates/{rc.id}", **headers)
        assert response.status_code == 404

    def test_rate_card_lookup(self, api, vendor_user, vendor, entry, unit):
        rc = RateCard.objects.create(name="Lookup RC", vendor=vendor)
        RateCardItem.objects.create(
            rate_card=rc,
            entry=entry,
            unit=unit,
            price=750,
        )
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.get(
            f"/api/v1/rates/lookup?entryId={entry.id}",
            **headers,
        )
        assert response.status_code == 200
        data = json.loads(response.content)
        assert len(data) >= 1


class TestProjectArchivedAndRestore:
    def test_archived_projects_list(self, api, vendor_user, vendor, project):
        project.deleted_at = timezone.now()
        project.save()
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.get("/api/v1/projects/archived", **headers)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert len(data) >= 1

    def test_restore_project(self, api, vendor_user, vendor, project):
        project.deleted_at = timezone.now()
        project.save()
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.post(
            f"/api/v1/projects/{project.id}/restore",
            **headers,
        )
        assert response.status_code == 200
        project.refresh_from_db()
        assert project.deleted_at is None

    def test_restore_nonexistent_project(self, api, vendor_user, vendor):
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.post(
            f"/api/v1/projects/{uuid.uuid4()}/restore",
            **headers,
        )
        assert response.status_code == 404


class TestXlsxUpload:
    def _make_xlsx(self, cell_value):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws["A1"] = cell_value
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf.read()

    def test_upload_xlsx_finds_offer(
        self, api, client_user, client_entity, published_offer, share
    ):
        xlsx_data = self._make_xlsx(str(published_offer.id))
        headers = _client_auth(client_user, client_entity.id)
        response = api.post(
            "/api/v1/client/xlsx-upload",
            data={
                "file": SimpleUploadedFile(
                    "test.xlsx",
                    xlsx_data,
                    content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            **headers,
        )
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["offer_id"] == str(published_offer.id)
        assert data["has_share"] is True
        assert data["share_token"] == share.token

    def test_upload_xlsx_no_file(self, api, client_user, client_entity):
        headers = _client_auth(client_user, client_entity.id)
        response = api.post(
            "/api/v1/client/xlsx-upload",
            **headers,
        )
        assert response.status_code == 400

    def test_upload_xlsx_no_offer_found(self, api, client_user, client_entity):
        xlsx_data = self._make_xlsx("just some text")
        headers = _client_auth(client_user, client_entity.id)
        response = api.post(
            "/api/v1/client/xlsx-upload",
            data={
                "file": SimpleUploadedFile(
                    "test.xlsx",
                    xlsx_data,
                    content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            **headers,
        )
        assert response.status_code == 404

    def test_upload_xlsx_offer_without_share(
        self, api, client_user, client_entity, offer
    ):
        xlsx_data = self._make_xlsx(str(offer.id))
        headers = _client_auth(client_user, client_entity.id)
        response = api.post(
            "/api/v1/client/xlsx-upload",
            data={
                "file": SimpleUploadedFile(
                    "test.xlsx",
                    xlsx_data,
                    content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            **headers,
        )
        assert response.status_code == 403

    def test_upload_invalid_file(self, api, client_user, client_entity):
        headers = _client_auth(client_user, client_entity.id)
        response = api.post(
            "/api/v1/client/xlsx-upload",
            data={
                "file": SimpleUploadedFile(
                    "test.xlsx",
                    b"not an xlsx",
                    content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            **headers,
        )
        assert response.status_code == 400


class TestOfferExportData:
    def test_get_export_data(self, api, vendor_user, vendor, published_offer):
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.get(
            f"/api/v1/offers/{published_offer.id}/export-data",
            **headers,
        )
        assert response.status_code == 200
        data = json.loads(response.content)
        assert "offer" in data
        assert "project" in data
        assert "vendor" in data
        assert "categories" in data

    def test_get_export_data_nonexistent(self, api, vendor_user, vendor):
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.get(
            f"/api/v1/offers/{uuid.uuid4()}/export-data",
            **headers,
        )
        assert response.status_code == 404

    def test_share_export_data(self, api, share):
        response = api.get(f"/api/v1/shares/{share.token}/export-data")
        assert response.status_code == 200
        data = json.loads(response.content)
        assert "offer" in data
        assert "categories" in data

    def test_share_export_data_not_found(self, api, db):
        response = api.get("/api/v1/shares/nonexistent/export-data")
        assert response.status_code == 404

    def test_share_export_data_does_not_leak_offer_cost(self, api, share):
        share.offer.cost = 12345.67
        share.offer.save(update_fields=["cost"])
        response = api.get(f"/api/v1/shares/{share.token}/export-data")
        assert response.status_code == 200
        data = json.loads(response.content)
        assert "cost" not in data["offer"], (
            "Public share endpoint must not expose internal offer.cost"
        )

    def test_offer_export_data_includes_cost_for_owner(
        self, api, vendor_user, vendor, published_offer
    ):
        published_offer.cost = 999.99
        published_offer.save(update_fields=["cost"])
        headers = _vendor_auth(vendor_user, vendor.id)
        response = api.get(
            f"/api/v1/offers/{published_offer.id}/export-data",
            **headers,
        )
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["offer"]["cost"] == 999.99

    def test_share_export_data_estimate_does_not_fall_back_to_internal_cost(
        self, api, share
    ):
        category = Category.objects.create(name="Cat", code="C1", level=0)
        OfferEntry.objects.create(
            offer=share.offer,
            category=category,
            item_name="Internal-only entry",
            cost=500,
            client_cost=None,
            sort_order=0,
        )
        response = api.get(f"/api/v1/shares/{share.token}/export-data")
        assert response.status_code == 200
        data = json.loads(response.content)
        category_payload = next(
            (c for c in data["categories"] if c["code"] == "C1"),
            None,
        )
        assert category_payload is not None
        entry = category_payload["entries"][0]
        assert entry["estimate"] == 0.0, (
            "Public estimate must not fall back to internal cost"
        )


class TestClientBriefsAPI:
    def test_list_client_briefs(self, api, client_user, client_entity, brief):
        headers = _client_auth(client_user, client_entity.id)
        response = api.get("/api/v1/client/briefs", **headers)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert len(data) >= 1

    def test_create_client_brief(self, api, client_user, client_entity):
        headers = _client_auth(client_user, client_entity.id)
        response = api.post(
            "/api/v1/client/briefs",
            data=json.dumps(
                {
                    "details": {"projectName": "New Client Brief"},
                }
            ),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 201
        data = json.loads(response.content)
        assert data["details"]["projectName"] == "New Client Brief"

    def test_get_client_brief_detail(self, api, client_user, client_entity, brief):
        headers = _client_auth(client_user, client_entity.id)
        response = api.get(f"/api/v1/client/briefs/{brief.id}", **headers)
        assert response.status_code == 200

    def test_update_client_brief(self, api, client_user, client_entity, brief):
        headers = _client_auth(client_user, client_entity.id)
        response = api.patch(
            f"/api/v1/client/briefs/{brief.id}",
            data=json.dumps({"details": {"projectName": "Updated"}}),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["details"]["projectName"] == "Updated"

    def test_delete_client_brief(self, api, client_user, client_entity, brief):
        headers = _client_auth(client_user, client_entity.id)
        response = api.delete(f"/api/v1/client/briefs/{brief.id}", **headers)
        assert response.status_code == 200
        brief.refresh_from_db()
        assert brief.deleted_at is not None

    def test_get_client_brief_wrong_client(self, api, db, brief):
        other_user = User.objects.create_user(
            email="wrong-client@example.com",
            password="pass",
            name="WrongC",
            group="CLIENT",
        )
        other_client = ClientModel.objects.create(name="WrongCo", owner=other_user)
        headers = _client_auth(other_user, other_client.id)
        response = api.get(f"/api/v1/client/briefs/{brief.id}", **headers)
        assert response.status_code in (403, 404)

    def test_client_brief_offers(
        self, api, client_user, client_entity, brief, published_offer
    ):
        BriefOffer.objects.create(
            brief=brief, offer=published_offer, linked_by=client_user
        )
        headers = _client_auth(client_user, client_entity.id)
        response = api.get(f"/api/v1/client/briefs/{brief.id}/offers", **headers)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert len(data) >= 1


class TestClientBriefComparisonAnalyze:
    def test_comparison_analyze_passes_history_to_llm(
        self, api, client_user, client_entity, brief, published_offer, monkeypatch
    ):
        BriefOffer.objects.create(
            brief=brief, offer=published_offer, linked_by=client_user
        )

        captured = {}

        def fake_analyze(brief_data, comparison_data, question=None, history=None):
            captured["question"] = question
            captured["history"] = history
            return {"analysis": "ok", "highlights": []}

        monkeypatch.setattr(
            "aivus_backend.projects.api.views.analyze_comparison",
            fake_analyze,
        )
        headers = _client_auth(client_user, client_entity.id)
        response = api.post(
            f"/api/v1/client/briefs/{brief.id}/comparison/analyze",
            data=json.dumps(
                {
                    "question": "which is best?",
                    "history": [
                        {"role": "user", "content": "first"},
                        {"role": "assistant", "content": "answer"},
                        {"role": "spam", "content": "ignored"},
                        {"role": "user", "content": 42},
                    ],
                }
            ),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 200
        assert captured["question"] == "which is best?"
        assert captured["history"] == [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "answer"},
        ]
