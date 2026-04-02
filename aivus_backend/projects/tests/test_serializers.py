"""Tests for projects API serializers."""

from decimal import Decimal

import pytest
from django.utils import timezone

from aivus_backend.catalog.models import Category
from aivus_backend.catalog.models import Entry
from aivus_backend.catalog.models import Unit
from aivus_backend.projects.api.serializers import serialize_brief
from aivus_backend.projects.api.serializers import serialize_brief_detail
from aivus_backend.projects.api.serializers import serialize_brief_offer
from aivus_backend.projects.api.serializers import serialize_brief_with_offers
from aivus_backend.projects.api.serializers import serialize_offer
from aivus_backend.projects.api.serializers import serialize_offer_for_client
from aivus_backend.projects.api.serializers import serialize_project
from aivus_backend.projects.api.serializers import serialize_rate_card
from aivus_backend.projects.api.serializers import serialize_rate_card_item
from aivus_backend.projects.api.serializers import serialize_share
from aivus_backend.projects.api.serializers import serialize_share_public
from aivus_backend.projects.api.serializers import serialize_template
from aivus_backend.projects.models import Brief
from aivus_backend.projects.models import BriefOffer
from aivus_backend.projects.models import Offer
from aivus_backend.projects.models import OfferDeliverable
from aivus_backend.projects.models import Project
from aivus_backend.projects.models import ProjectCollaborator
from aivus_backend.projects.models import RateCard
from aivus_backend.projects.models import RateCardItem
from aivus_backend.projects.models import Share
from aivus_backend.projects.models import Template
from aivus_backend.users.models import Client as ClientModel
from aivus_backend.users.models import User
from aivus_backend.users.models import Vendor


@pytest.fixture
def vendor_user(db):
    return User.objects.create_user(
        email="ser-vendor@example.com",
        password="pass",
        name="SerVendor",
        group="VENDOR",
    )


@pytest.fixture
def client_user(db):
    return User.objects.create_user(
        email="ser-client@example.com",
        password="pass",
        name="SerClient",
        group="CLIENT",
    )


@pytest.fixture
def vendor(vendor_user):
    return Vendor.objects.create(name="Ser Agency", owner=vendor_user)


@pytest.fixture
def client_entity(client_user):
    return ClientModel.objects.create(name="Ser Company", owner=client_user)


@pytest.fixture
def category(db):
    return Category.objects.create(name="Ser Cat", level=1, tags=["production"])


@pytest.fixture
def entry(category):
    return Entry.objects.create(name="Ser Entry", category=category, is_approved=True)


@pytest.fixture
def unit(db):
    return Unit.objects.create(name="Day", symbol="Days", dimension="TEMPORAL")


@pytest.fixture
def project(vendor):
    return Project.objects.create(name="Ser Project", vendor=vendor, status="DRAFT")


@pytest.fixture
def offer(project):
    return Offer.objects.create(
        project_name="Ser Offer",
        project=project,
        status="PUBLISHED",
        details={"offers": []},
        deadline=timezone.now(),
        source="PLATFORM",
        cost=Decimal("5000"),
        profit=Decimal("1000"),
    )


@pytest.fixture
def brief(client_entity):
    return Brief.objects.create(
        status="DRAFT", details={"projectName": "Ser Brief"}, client=client_entity
    )


class TestSerializeProject:
    def test_basic_fields(self, project):
        data = serialize_project(project, include_relations=False)
        assert data["id"] == str(project.id)
        assert data["name"] == "Ser Project"
        assert data["status"] == "DRAFT"
        assert data["vendorId"] == str(project.vendor_id)

    def test_includes_collaborators(self, project):
        ProjectCollaborator.objects.create(
            project=project, name="John", email="john@test.com", role="producer"
        )
        data = serialize_project(project, include_relations=True)
        assert len(data["collaborators"]) == 1
        assert data["collaborators"][0]["name"] == "John"

    def test_includes_client_managers(self, project):
        from aivus_backend.projects.models import ClientManager

        ClientManager.objects.create(project=project, name="Jane", position="VP")
        data = serialize_project(project, include_relations=True)
        assert len(data["clientManagers"]) == 1
        assert data["clientManagers"][0]["name"] == "Jane"
        assert data["clientManagers"][0]["position"] == "VP"

    def test_null_optional_fields(self, project):
        data = serialize_project(project, include_relations=False)
        assert data["briefId"] is None
        assert data["clientId"] is None
        assert data["agencyName"] in (None, "")


class TestSerializeOffer:
    def test_basic_fields(self, offer):
        data = serialize_offer(offer)
        assert data["id"] == str(offer.id)
        assert data["uuid"] == str(offer.id)
        assert data["projectName"] == "Ser Offer"
        assert data["status"] == "PUBLISHED"
        assert data["cost"] == 5000.0
        assert data["profit"] == 1000.0

    def test_meta_fields(self, offer):
        data = serialize_offer(offer)
        assert "fringesPercent" in data
        assert "handlingPercent" in data
        assert "deliverables" in data
        assert "scheduleEntries" in data

    def test_deliverables_included(self, offer):
        OfferDeliverable.objects.create(
            offer=offer,
            quantity=2,
            duration="30",
            duration_unit="sec",
            notes="Test del",
            sort_order=0,
        )
        data = serialize_offer(offer)
        assert len(data["deliverables"]) == 1
        assert data["deliverables"][0]["quantity"] == 2

    def test_soft_deleted_deliverables_excluded(self, offer):
        d = OfferDeliverable.objects.create(
            offer=offer,
            quantity=1,
            duration="15",
            duration_unit="sec",
            sort_order=0,
        )
        d.deleted_at = timezone.now()
        d.save()
        data = serialize_offer(offer)
        assert len(data["deliverables"]) == 0


class TestSerializeOfferForClient:
    def test_excludes_cost_and_profit(self, offer):
        data = serialize_offer_for_client(offer)
        assert "cost" not in data
        assert "profit" not in data
        assert data["id"] == str(offer.id)
        assert data["projectName"] == "Ser Offer"


class TestSerializeShare:
    def test_basic_fields(self, offer, vendor_user):
        share = Share.objects.create(offer=offer, created_by=vendor_user)
        data = serialize_share(share)
        assert data["offerId"] == str(offer.id)
        assert data["token"] == share.token
        assert data["isActive"] is True
        assert data["createdBy"] == str(vendor_user.id)

    def test_created_by_null(self, offer):
        share = Share.objects.create(offer=offer, created_by=None)
        data = serialize_share(share)
        assert data["createdBy"] is None


class TestSerializeSharePublic:
    def test_includes_offer_and_vendor(self, offer, vendor_user):
        share = Share.objects.create(offer=offer, created_by=vendor_user)
        data = serialize_share_public(share)
        assert "offer" in data
        assert data["offer"]["projectName"] == "Ser Offer"
        assert data["vendor"]["name"] == "Ser Agency"
        assert data["token"] == share.token


class TestSerializeBrief:
    def test_basic_fields(self, brief):
        data = serialize_brief(brief)
        assert data["id"] == str(brief.id)
        assert data["status"] == "DRAFT"
        assert data["details"]["projectName"] == "Ser Brief"
        assert data["clientId"] == str(brief.client_id)


class TestSerializeBriefWithOffers:
    def test_includes_offers_count(self, brief, offer, client_user):
        BriefOffer.objects.create(brief=brief, offer=offer, linked_by=client_user)
        data = serialize_brief_with_offers(brief)
        assert data["offersCount"] == 1

    def test_zero_offers(self, brief):
        data = serialize_brief_with_offers(brief)
        assert data["offersCount"] == 0


class TestSerializeBriefDetail:
    def test_includes_linked_offers(self, brief, offer, client_user):
        BriefOffer.objects.create(brief=brief, offer=offer, linked_by=client_user)
        data = serialize_brief_detail(brief)
        assert len(data["offers"]) == 1
        assert data["offers"][0]["projectName"] == "Ser Offer"
        assert data["offers"][0]["vendor"]["name"] == "Ser Agency"

    def test_empty_offers(self, brief):
        data = serialize_brief_detail(brief)
        assert len(data["offers"]) == 0
        assert data["offersCount"] == 0


class TestSerializeBriefOffer:
    def test_basic_fields(self, brief, offer, client_user):
        bo = BriefOffer.objects.create(brief=brief, offer=offer, linked_by=client_user)
        data = serialize_brief_offer(bo)
        assert data["briefId"] == str(brief.id)
        assert data["offerId"] == str(offer.id)
        assert data["linkedBy"] == str(client_user.id)


class TestSerializeTemplate:
    def test_basic_fields(self, vendor, offer):
        template = Template.objects.create(
            name="T1",
            vendor=vendor,
            source_offer=offer,
            details={"offers": []},
            metadata={"cats": []},
        )
        data = serialize_template(template)
        assert data["id"] == str(template.id)
        assert data["name"] == "T1"
        assert data["vendorId"] == str(vendor.id)
        assert data["sourceOfferId"] == str(offer.id)
        assert data["details"] == {"offers": []}

    def test_null_source_offer(self, vendor):
        template = Template.objects.create(
            name="T2",
            vendor=vendor,
            details={},
            metadata={},
        )
        data = serialize_template(template)
        assert data["sourceOfferId"] is None


class TestSerializeRateCard:
    def test_basic_fields(self, vendor):
        rc = RateCard.objects.create(name="RC", vendor=vendor)
        data = serialize_rate_card(rc, include_items=False)
        assert data["name"] == "RC"
        assert data["vendorId"] == str(vendor.id)
        assert "items" not in data

    def test_includes_items(self, vendor, entry, unit):
        rc = RateCard.objects.create(name="RC", vendor=vendor)
        RateCardItem.objects.create(
            rate_card=rc, entry=entry, unit=unit, price=Decimal("750")
        )
        data = serialize_rate_card(rc, include_items=True)
        assert len(data["items"]) == 1
        assert data["items"][0]["price"] == "750.00"

    def test_soft_deleted_items_excluded(self, vendor, entry, unit):
        rc = RateCard.objects.create(name="RC", vendor=vendor)
        item = RateCardItem.objects.create(
            rate_card=rc, entry=entry, unit=unit, price=Decimal("100")
        )
        item.deleted_at = timezone.now()
        item.save()
        data = serialize_rate_card(rc, include_items=True)
        assert len(data["items"]) == 0


class TestSerializeRateCardItem:
    def test_basic_fields(self, vendor, entry, unit):
        rc = RateCard.objects.create(name="RC", vendor=vendor)
        item = RateCardItem.objects.create(
            rate_card=rc,
            entry=entry,
            unit=unit,
            price=Decimal("500.50"),
            item_name="Custom Name",
        )
        data = serialize_rate_card_item(item)
        assert data["price"] == "500.50"
        assert data["entryId"] == str(entry.id)
        assert data["unitId"] == str(unit.id)
        assert data["itemName"] == "Custom Name"
