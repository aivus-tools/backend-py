"""Tests for Offer.details migration to OfferEntry/OfferRate models.

These tests verify:
1. Offer model creation with JSON details
2. OfferEntry creation and linkage to Offer and Entry
3. OfferRate creation with rate snapshot data
4. Empty/invalid details graceful handling
5. Unique constraints on OfferEntry and OfferRate
6. Cascade delete behavior
7. Complex JSON structure handling
"""

import pytest
from decimal import Decimal
from django.db import IntegrityError
from django.utils import timezone

from aivus_backend.catalog.models import Category, Entry, Unit
from aivus_backend.projects.models import (
    Brief,
    Offer,
    OfferEntry,
    OfferRate,
    Project,
    Rate,
)
from aivus_backend.users.models import User, Vendor


@pytest.fixture
def vendor_user(db):
    """Create a vendor user."""
    return User.objects.create_user(
        email="vendor-test@example.com",
        password="testpass123",
        name="Test Vendor User",
        group="VENDOR",
    )


@pytest.fixture
def vendor(vendor_user):
    """Create a vendor."""
    return Vendor.objects.create(
        name="Test Agency",
        owner=vendor_user,
    )


@pytest.fixture
def category(db):
    """Create a test category."""
    return Category.objects.create(
        name="Test Category",
        level=1,
    )


@pytest.fixture
def entry(category):
    """Create a test entry."""
    return Entry.objects.create(
        name="Test Entry",
        short_description="Test short desc",
        description="Test description",
        is_approved=True,
        category=category,
    )


@pytest.fixture
def entry2(category):
    """Create a second test entry."""
    return Entry.objects.create(
        name="Test Entry 2",
        short_description="Second entry",
        description="Second test description",
        is_approved=True,
        category=category,
    )


@pytest.fixture
def project(vendor):
    """Create a test project."""
    return Project.objects.create(
        name="Test Project",
        vendor=vendor,
        status="DRAFT",
    )


@pytest.fixture
def rate(vendor, entry):
    """Create a test rate."""
    return Rate.objects.create(
        name="Test Rate",
        description="Test rate description",
        vendor=vendor,
        entry=entry,
        base_price=Decimal("100.00"),
        total_price=Decimal("100.00"),
        options=[],
    )


@pytest.fixture
def offer(project):
    """Create a test offer with empty details."""
    return Offer.objects.create(
        project_name="Test Offer",
        project=project,
        status="DRAFT",
        details={},
        deadline=timezone.now(),
        source="PLATFORM",
    )


@pytest.fixture
def offer_with_details(project):
    """Create a test offer with realistic details JSON."""
    details = {
        "offers": [
            {
                "id": "123456",
                "cost": 500,
                "item": "Scriptwriting",
                "price": 500,
                "units": [
                    {
                        "type": "quantity",
                        "count": 1,
                        "label": "Each",
                        "value": "unit-uuid-1",
                        "isDefault": True,
                    }
                ],
                "entryId": "entry-uuid-1",
                "options": {
                    "time": [],
                    "quantity": [
                        {
                            "type": "quantity",
                            "count": 1,
                            "label": "Each",
                            "value": "unit-uuid-1",
                            "isDefault": True,
                        }
                    ],
                },
                "showTax": False,
                "taxRate": 0,
                "taxPrice": 500,
                "surcharge": 10,
                "categoryId": "category-uuid-1",
                "clientCost": 550.00,
                "clientPrice": 550.00,
                "marketRange": "",
                "isLinkedSurcharge": True,
            }
        ]
    }
    return Offer.objects.create(
        project_name="Test Offer With Details",
        project=project,
        status="DRAFT",
        details=details,
        deadline=timezone.now(),
        source="PLATFORM",
    )


# ==================== Model Creation Tests ====================


class TestOfferModel:
    """Tests for Offer model."""

    def test_offer_creation_with_empty_details(self, offer):
        """Test creating an Offer with empty details dict."""
        assert offer.id is not None
        assert offer.details == {}
        assert offer.project_name == "Test Offer"
        assert offer.status == "DRAFT"
        assert offer.source == "PLATFORM"
        assert offer.is_locked is False

    def test_offer_creation_with_details(self, offer_with_details):
        """Test creating an Offer with populated details JSON."""
        assert offer_with_details.id is not None
        assert "offers" in offer_with_details.details
        assert len(offer_with_details.details["offers"]) == 1
        first_offer = offer_with_details.details["offers"][0]
        assert first_offer["item"] == "Scriptwriting"
        assert first_offer["cost"] == 500

    def test_offer_details_json_structure(self, offer_with_details):
        """Test that details JSON maintains its structure."""
        offer = Offer.objects.get(id=offer_with_details.id)
        assert offer.details == offer_with_details.details
        first_entry = offer.details["offers"][0]
        assert "units" in first_entry
        assert "options" in first_entry
        assert "entryId" in first_entry
        assert "categoryId" in first_entry

    def test_offer_soft_delete(self, offer):
        """Test soft delete sets deleted_at timestamp."""
        assert offer.deleted_at is None
        offer.deleted_at = timezone.now()
        offer.save()
        # Soft-deleted offer should still exist in DB
        assert Offer.objects.filter(id=offer.id).exists()
        # But not in active queryset
        assert not Offer.objects.filter(
            id=offer.id, deleted_at__isnull=True
        ).exists()

    def test_offer_details_with_complex_json(self, project):
        """Test offer with deeply nested JSON details."""
        complex_details = {
            "offers": [
                {
                    "id": str(i),
                    "cost": i * 100,
                    "item": f"Item {i}",
                    "price": i * 100,
                    "units": [
                        {"type": "quantity", "count": j, "label": f"Unit {j}"}
                        for j in range(1, 4)
                    ],
                    "options": {
                        "time": [{"type": "time", "count": 1, "label": "Hour"}],
                        "quantity": [
                            {"type": "quantity", "count": 1, "label": "Each"}
                        ],
                    },
                }
                for i in range(1, 6)
            ]
        }
        offer = Offer.objects.create(
            project_name="Complex Offer",
            project=project,
            status="DRAFT",
            details=complex_details,
            deadline=timezone.now(),
            source="PLATFORM",
        )
        reloaded = Offer.objects.get(id=offer.id)
        assert len(reloaded.details["offers"]) == 5
        assert reloaded.details["offers"][2]["item"] == "Item 3"
        assert len(reloaded.details["offers"][0]["units"]) == 3


# ==================== OfferEntry Tests ====================


class TestOfferEntry:
    """Tests for OfferEntry model.

    QA4-014: Updated to use current OfferEntry fields (price, cost, item_name, etc.)
    and removed tests for non-existent constraints.
    """

    def test_offer_entry_creation(self, offer, entry):
        """Test creating an OfferEntry linking Offer to Entry."""
        offer_entry = OfferEntry.objects.create(
            offer=offer,
            entry=entry,
            item_name="Camera Operator",
            price=Decimal("500.00"),
            cost=Decimal("450.00"),
            client_price=Decimal("550.00"),
            surcharge=Decimal("10.00"),
            tax_rate=Decimal("6.00"),
            sort_order=0,
            item_data={"units": [{"type": "quantity", "count": 1}]},
        )
        assert offer_entry.id is not None
        assert offer_entry.offer == offer
        assert offer_entry.entry == entry
        assert offer_entry.price == Decimal("500.00")
        assert offer_entry.cost == Decimal("450.00")
        assert offer_entry.item_name == "Camera Operator"
        assert offer_entry.item_data["units"][0]["type"] == "quantity"

    def test_offer_entry_cascade_delete(self, offer, entry):
        """Test that hard-deleting an Offer cascades to OfferEntry."""
        OfferEntry.objects.create(
            offer=offer,
            entry=entry,
            item_name="Test Item",
            price=Decimal("500.00"),
            cost=Decimal("450.00"),
            sort_order=0,
        )
        offer_id = offer.id
        assert OfferEntry.objects.filter(offer_id=offer_id).count() == 1
        # Hard delete via queryset to trigger CASCADE
        Offer.objects.filter(id=offer_id).delete()
        assert OfferEntry.objects.filter(offer_id=offer_id).count() == 0

    def test_multiple_entries_per_offer(self, offer, entry, entry2):
        """Test that an offer can have multiple entries (no unique constraint on offer+entry)."""
        OfferEntry.objects.create(
            offer=offer,
            entry=entry,
            item_name="Entry 1",
            price=Decimal("500.00"),
            cost=Decimal("450.00"),
            sort_order=0,
        )
        OfferEntry.objects.create(
            offer=offer,
            entry=entry2,
            item_name="Entry 2",
            price=Decimal("300.00"),
            cost=Decimal("250.00"),
            sort_order=1,
        )
        assert offer.offer_entries.count() == 2

    def test_offer_entry_soft_delete(self, offer, entry):
        """Test soft delete sets deleted_at timestamp."""
        offer_entry = OfferEntry.objects.create(
            offer=offer,
            entry=entry,
            item_name="Soft Delete Test",
            price=Decimal("100.00"),
            sort_order=0,
        )
        assert offer_entry.deleted_at is None
        offer_entry.deleted_at = timezone.now()
        offer_entry.save()
        assert OfferEntry.objects.filter(id=offer_entry.id).exists()
        assert not OfferEntry.objects.filter(
            id=offer_entry.id, deleted_at__isnull=True
        ).exists()


# ==================== OfferRate Tests ====================


class TestOfferRate:
    """Tests for OfferRate model."""

    def test_offer_rate_creation_with_snapshot(self, offer, rate):
        """Test creating an OfferRate with rate snapshot data."""
        offer_rate = OfferRate.objects.create(
            offer=offer,
            rate=rate,
            name=rate.name,
            description=rate.description,
            base_price=rate.base_price,
            total_price=rate.total_price,
            options=rate.options,
            quantity=2,
        )
        assert offer_rate.id is not None
        assert offer_rate.name == "Test Rate"
        assert offer_rate.base_price == Decimal("100.00")
        assert offer_rate.quantity == 2

    def test_offer_rate_unique_constraint(self, offer, rate):
        """Test unique_together constraint on (offer, rate)."""
        OfferRate.objects.create(
            offer=offer,
            rate=rate,
            name=rate.name,
            description=rate.description,
            base_price=rate.base_price,
            total_price=rate.total_price,
            options=[],
        )
        with pytest.raises(IntegrityError):
            OfferRate.objects.create(
                offer=offer,
                rate=rate,
                name="Duplicate",
                description="",
                base_price=Decimal("200.00"),
                total_price=Decimal("200.00"),
                options=[],
            )

    def test_offer_rate_snapshot_independence(self, offer, rate):
        """Test that OfferRate snapshot is independent of Rate changes."""
        offer_rate = OfferRate.objects.create(
            offer=offer,
            rate=rate,
            name=rate.name,
            description=rate.description,
            base_price=rate.base_price,
            total_price=rate.total_price,
            options=rate.options,
        )
        # Update the original rate
        rate.name = "Updated Rate Name"
        rate.base_price = Decimal("999.99")
        rate.save()
        # Reload offer_rate
        offer_rate.refresh_from_db()
        # Snapshot should remain unchanged
        assert offer_rate.name == "Test Rate"
        assert offer_rate.base_price == Decimal("100.00")

    def test_offer_rate_with_options(self, offer, rate):
        """Test OfferRate with options array."""
        options = [
            {"name": "Rush delivery", "type": "fixed", "value": 50},
            {"name": "Tax", "type": "percentage", "value": 10},
        ]
        offer_rate = OfferRate.objects.create(
            offer=offer,
            rate=rate,
            name=rate.name,
            description=rate.description,
            base_price=rate.base_price,
            total_price=Decimal("165.00"),
            options=options,
        )
        reloaded = OfferRate.objects.get(id=offer_rate.id)
        assert len(reloaded.options) == 2
        assert reloaded.options[0]["name"] == "Rush delivery"
        assert reloaded.options[1]["type"] == "percentage"
