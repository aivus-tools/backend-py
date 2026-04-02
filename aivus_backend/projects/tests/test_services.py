import uuid
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from aivus_backend.catalog.models import Category
from aivus_backend.catalog.models import Entry
from aivus_backend.projects.models import Offer
from aivus_backend.projects.models import OfferEntry
from aivus_backend.projects.models import Project
from aivus_backend.projects.services import _calculate_category_client_fees
from aivus_backend.projects.services import _lookup_category
from aivus_backend.projects.services import _lookup_entry
from aivus_backend.projects.services import _to_decimal
from aivus_backend.projects.services import parse_offer_details_to_entries
from aivus_backend.projects.services import recalculate_offer_totals
from aivus_backend.projects.services import reconstruct_details_from_entries
from aivus_backend.users.models import User
from aivus_backend.users.models import Vendor


@pytest.fixture
def vendor_user(db):
    return User.objects.create_user(
        email="services-test@example.com",
        password="testpass123",
        name="Services Test User",
        group="VENDOR",
    )


@pytest.fixture
def vendor(vendor_user):
    return Vendor.objects.create(
        name="Services Test Agency",
        owner=vendor_user,
    )


@pytest.fixture
def project(vendor):
    return Project.objects.create(
        name="Services Test Project",
        vendor=vendor,
        status="DRAFT",
    )


@pytest.fixture
def category(db):
    return Category.objects.create(
        name="Production",
        level=1,
        tags=["production"],
    )


@pytest.fixture
def post_category(db):
    return Category.objects.create(
        name="Post-Production",
        level=1,
        tags=["post_production"],
    )


@pytest.fixture
def entry(category):
    return Entry.objects.create(
        name="Camera Operator",
        is_approved=True,
        category=category,
    )


@pytest.fixture
def offer(project):
    return Offer.objects.create(
        project_name="Test Offer",
        project=project,
        status="DRAFT",
        details={},
        deadline=timezone.now(),
        source="PLATFORM",
    )


class TestToDecimal:
    def test_none_returns_none(self):
        assert _to_decimal(None) is None

    def test_valid_int(self):
        assert _to_decimal(42) == Decimal("42")

    def test_valid_float(self):
        assert _to_decimal(3.14) == Decimal("3.14")

    def test_valid_string(self):
        assert _to_decimal("99.99") == Decimal("99.99")

    def test_invalid_string(self):
        assert _to_decimal("not-a-number") is None

    def test_empty_string(self):
        assert _to_decimal("") is None

    def test_decimal_passthrough(self):
        value = Decimal("123.45")
        assert _to_decimal(value) == Decimal("123.45")

    def test_zero(self):
        assert _to_decimal(0) == Decimal("0")

    def test_negative(self):
        assert _to_decimal(-50) == Decimal("-50")


class TestLookupEntry:
    def test_valid_uuid_finds_entry(self, entry):
        result = _lookup_entry(str(entry.id))
        assert result == entry

    def test_invalid_uuid_raises_validation_error(self, db):
        with pytest.raises(ValidationError):
            _lookup_entry("not-a-uuid")

    def test_empty_string_returns_none(self, db):
        assert _lookup_entry("") is None

    def test_none_returns_none(self, db):
        assert _lookup_entry(None) is None

    def test_nonexistent_uuid_returns_none(self, db):
        assert _lookup_entry(str(uuid.uuid4())) is None


class TestLookupCategory:
    def test_valid_uuid_finds_category(self, category):
        result = _lookup_category(str(category.id))
        assert result == category

    def test_invalid_uuid_raises_validation_error(self, db):
        with pytest.raises(ValidationError):
            _lookup_category("not-a-uuid")

    def test_empty_string_returns_none(self, db):
        assert _lookup_category("") is None

    def test_none_returns_none(self, db):
        assert _lookup_category(None) is None

    def test_nonexistent_uuid_returns_none(self, db):
        assert _lookup_category(str(uuid.uuid4())) is None


class TestCalculateCategoryClientFees:
    def _make_offer(self, project, **kwargs):
        defaults = {
            "project_name": "Fee Test",
            "project": project,
            "status": "DRAFT",
            "details": {},
            "source": "PLATFORM",
            "production_insurance_percent": Decimal("0"),
            "production_fee_percent": Decimal("0"),
            "post_markup_percent": Decimal("0"),
            "post_insurance_percent": Decimal("0"),
            "post_tax_percent": Decimal("0"),
        }
        defaults.update(kwargs)
        return Offer.objects.create(**defaults)

    def test_no_categories_returns_zero(self, project):
        offer = self._make_offer(project)
        details: dict = {"categories": [], "offers": []}
        result = _calculate_category_client_fees(offer, details)
        assert result == Decimal("0")

    def test_empty_details_returns_zero(self, project):
        offer = self._make_offer(project)
        result = _calculate_category_client_fees(offer, {})
        assert result == Decimal("0")

    def test_production_with_insurance_only(self, project):
        offer = self._make_offer(
            project,
            production_insurance_percent=Decimal("10"),
        )
        details = {
            "categories": [{"id": "cat-1", "tags": ["production"]}],
            "subCategories": [],
            "offers": [
                {"categoryId": "cat-1", "clientCost": 1000},
            ],
        }
        result = _calculate_category_client_fees(offer, details)
        assert result == Decimal("100")

    def test_production_with_fee_only(self, project):
        offer = self._make_offer(
            project,
            production_fee_percent=Decimal("15"),
        )
        details = {
            "categories": [{"id": "cat-1", "tags": ["production"]}],
            "subCategories": [],
            "offers": [
                {"categoryId": "cat-1", "clientCost": 2000},
            ],
        }
        result = _calculate_category_client_fees(offer, details)
        assert result == Decimal("300")

    def test_production_with_both_insurance_and_fee(self, project):
        offer = self._make_offer(
            project,
            production_insurance_percent=Decimal("10"),
            production_fee_percent=Decimal("15"),
        )
        details = {
            "categories": [{"id": "cat-1", "tags": ["production"]}],
            "subCategories": [],
            "offers": [
                {"categoryId": "cat-1", "clientCost": 1000},
            ],
        }
        result = _calculate_category_client_fees(offer, details)
        expected = (
            Decimal("1000") * Decimal("10") / 100
            + Decimal("1000") * Decimal("15") / 100
        )
        assert result == expected

    def test_production_with_external_markup_skips_fee(self, project):
        offer = self._make_offer(
            project,
            production_insurance_percent=Decimal("10"),
            production_fee_percent=Decimal("15"),
        )
        details = {
            "categories": [{"id": "cat-1", "tags": ["production"]}],
            "subCategories": [],
            "offers": [
                {"categoryId": "cat-1", "clientCost": 1000},
            ],
            "categoryExternalMarkup": {
                "cat-1": {"enabled": True, "percent": 20},
            },
        }
        result = _calculate_category_client_fees(offer, details)
        insurance = Decimal("1000") * Decimal("10") / 100
        external = Decimal("1000") * Decimal("20") / 100
        assert result == insurance + external

    def test_post_production_with_insurance_markup_tax(self, project):
        offer = self._make_offer(
            project,
            post_insurance_percent=Decimal("5"),
            post_markup_percent=Decimal("10"),
            post_tax_percent=Decimal("8"),
        )
        details = {
            "categories": [{"id": "cat-1", "tags": ["post_production"]}],
            "subCategories": [],
            "offers": [
                {"categoryId": "cat-1", "clientCost": 2000},
            ],
        }
        result = _calculate_category_client_fees(offer, details)
        base = Decimal("2000")
        expected = (
            base * Decimal("5") / 100
            + base * Decimal("10") / 100
            + base * Decimal("8") / 100
        )
        assert result == expected

    def test_post_production_with_external_markup_skips_markup(self, project):
        offer = self._make_offer(
            project,
            post_insurance_percent=Decimal("5"),
            post_markup_percent=Decimal("10"),
            post_tax_percent=Decimal("8"),
        )
        details = {
            "categories": [{"id": "cat-1", "tags": ["post_production"]}],
            "subCategories": [],
            "offers": [
                {"categoryId": "cat-1", "clientCost": 2000},
            ],
            "categoryExternalMarkup": {
                "cat-1": {"enabled": True, "percent": 25},
            },
        }
        result = _calculate_category_client_fees(offer, details)
        base = Decimal("2000")
        insurance = base * Decimal("5") / 100
        tax = base * Decimal("8") / 100
        external = base * Decimal("25") / 100
        assert result == insurance + tax + external

    def test_mixed_production_and_post_production(self, project):
        offer = self._make_offer(
            project,
            production_insurance_percent=Decimal("10"),
            post_markup_percent=Decimal("20"),
        )
        details = {
            "categories": [
                {"id": "cat-prod", "tags": ["production"]},
                {"id": "cat-post", "tags": ["post_production"]},
            ],
            "subCategories": [],
            "offers": [
                {"categoryId": "cat-prod", "clientCost": 1000},
                {"categoryId": "cat-post", "clientCost": 500},
            ],
        }
        result = _calculate_category_client_fees(offer, details)
        prod_fee = Decimal("1000") * Decimal("10") / 100
        post_fee = Decimal("500") * Decimal("20") / 100
        assert result == prod_fee + post_fee

    def test_subcategory_costs_included_in_parent(self, project):
        offer = self._make_offer(
            project,
            production_insurance_percent=Decimal("10"),
        )
        details = {
            "categories": [{"id": "cat-parent", "tags": ["production"]}],
            "subCategories": [
                {"id": "sub-1", "parentCategoryId": "cat-parent"},
            ],
            "offers": [
                {"categoryId": "cat-parent", "clientCost": 1000},
                {"categoryId": "sub-1", "clientCost": 500},
            ],
        }
        result = _calculate_category_client_fees(offer, details)
        total_client = Decimal("1500")
        assert result == total_client * Decimal("10") / 100

    def test_zero_percents_no_fees(self, project):
        offer = self._make_offer(project)
        details = {
            "categories": [
                {"id": "cat-1", "tags": ["production"]},
                {"id": "cat-2", "tags": ["post_production"]},
            ],
            "subCategories": [],
            "offers": [
                {"categoryId": "cat-1", "clientCost": 5000},
                {"categoryId": "cat-2", "clientCost": 3000},
            ],
        }
        result = _calculate_category_client_fees(offer, details)
        assert result == Decimal("0")

    def test_external_markup_disabled_not_applied(self, project):
        offer = self._make_offer(
            project,
            production_fee_percent=Decimal("15"),
        )
        details = {
            "categories": [{"id": "cat-1", "tags": ["production"]}],
            "subCategories": [],
            "offers": [
                {"categoryId": "cat-1", "clientCost": 1000},
            ],
            "categoryExternalMarkup": {
                "cat-1": {"enabled": False, "percent": 20},
            },
        }
        result = _calculate_category_client_fees(offer, details)
        assert result == Decimal("1000") * Decimal("15") / 100

    def test_external_markup_zero_percent_not_applied(self, project):
        offer = self._make_offer(
            project,
            production_fee_percent=Decimal("15"),
        )
        details = {
            "categories": [{"id": "cat-1", "tags": ["production"]}],
            "subCategories": [],
            "offers": [
                {"categoryId": "cat-1", "clientCost": 1000},
            ],
            "categoryExternalMarkup": {
                "cat-1": {"enabled": True, "percent": 0},
            },
        }
        result = _calculate_category_client_fees(offer, details)
        assert result == Decimal("1000") * Decimal("15") / 100


class TestRecalculateOfferTotals:
    def test_no_entries_zero_cost_and_profit(self, offer):
        recalculate_offer_totals(offer)
        offer.refresh_from_db()
        assert offer.cost == Decimal("0")
        assert offer.profit == Decimal("0")

    def test_entries_without_unforeseen(self, offer):
        OfferEntry.objects.create(
            offer=offer,
            item_name="Item A",
            cost=Decimal("1000.00"),
            client_cost=Decimal("1200.00"),
            sort_order=0,
        )
        OfferEntry.objects.create(
            offer=offer,
            item_name="Item B",
            cost=Decimal("500.00"),
            client_cost=Decimal("600.00"),
            sort_order=1,
        )
        recalculate_offer_totals(offer)
        offer.refresh_from_db()
        assert offer.cost == Decimal("1500.00")

    def test_entries_with_unforeseen_expenses(self, offer):
        offer.details = {
            "unforeseenExpenses": {"isVisible": True, "percent": 10},
        }
        offer.save(update_fields=["details"])
        OfferEntry.objects.create(
            offer=offer,
            item_name="Item A",
            cost=Decimal("1000.00"),
            client_cost=Decimal("1200.00"),
            sort_order=0,
        )
        recalculate_offer_totals(offer)
        offer.refresh_from_db()
        assert offer.cost == Decimal("1100.00")

    def test_unforeseen_not_visible_no_addition(self, offer):
        offer.details = {
            "unforeseenExpenses": {"isVisible": False, "percent": 10},
        }
        offer.save(update_fields=["details"])
        OfferEntry.objects.create(
            offer=offer,
            item_name="Item A",
            cost=Decimal("1000.00"),
            client_cost=Decimal("1200.00"),
            sort_order=0,
        )
        recalculate_offer_totals(offer)
        offer.refresh_from_db()
        assert offer.cost == Decimal("1000.00")

    def test_profit_equals_client_total_minus_cost(self, offer):
        OfferEntry.objects.create(
            offer=offer,
            item_name="Item A",
            cost=Decimal("800.00"),
            client_cost=Decimal("1200.00"),
            sort_order=0,
        )
        recalculate_offer_totals(offer)
        offer.refresh_from_db()
        assert offer.profit == Decimal("1200.00") - Decimal("800.00")

    def test_with_client_fees_from_categories(self, offer):
        offer.production_insurance_percent = Decimal("10")
        offer.details = {
            "categories": [{"id": "cat-1", "tags": ["production"]}],
            "subCategories": [],
            "offers": [
                {"categoryId": "cat-1", "clientCost": 1000},
            ],
        }
        offer.save(update_fields=["details", "production_insurance_percent"])
        OfferEntry.objects.create(
            offer=offer,
            item_name="Item A",
            cost=Decimal("800.00"),
            client_cost=Decimal("1000.00"),
            sort_order=0,
        )
        recalculate_offer_totals(offer)
        offer.refresh_from_db()
        client_fees = Decimal("1000") * Decimal("10") / 100
        client_total = Decimal("1000.00") + client_fees
        assert offer.profit == client_total - offer.cost

    def test_soft_deleted_entries_excluded(self, offer):
        OfferEntry.objects.create(
            offer=offer,
            item_name="Active",
            cost=Decimal("1000.00"),
            client_cost=Decimal("1200.00"),
            sort_order=0,
        )
        OfferEntry.objects.create(
            offer=offer,
            item_name="Deleted",
            cost=Decimal("500.00"),
            client_cost=Decimal("600.00"),
            sort_order=1,
            deleted_at=timezone.now(),
        )
        recalculate_offer_totals(offer)
        offer.refresh_from_db()
        assert offer.cost == Decimal("1000.00")


class TestParseOfferDetailsToEntries:
    def test_empty_details_returns_none(self, offer):
        assert parse_offer_details_to_entries(offer, {}) is None

    def test_none_details_returns_none(self, offer):
        assert parse_offer_details_to_entries(offer, None) is None

    def test_non_dict_details_returns_none(self, offer):
        assert parse_offer_details_to_entries(offer, "string") is None
        assert parse_offer_details_to_entries(offer, [1, 2, 3]) is None

    def test_offers_not_a_list_returns_none(self, offer):
        assert parse_offer_details_to_entries(offer, {"offers": "bad"}) is None

    def test_valid_single_offer_creates_one_entry(self, offer):
        details = {
            "offers": [
                {
                    "id": "fe-1",
                    "item": "Camera Operator",
                    "price": 500,
                    "cost": 400,
                    "clientPrice": 600,
                    "clientCost": 550,
                    "surcharge": 10,
                    "taxRate": 6,
                    "taxPrice": 33,
                    "showTax": False,
                    "overtime": 0,
                    "isLinkedSurcharge": True,
                    "marketRange": "mid",
                },
            ],
        }
        count = parse_offer_details_to_entries(offer, details)
        assert count == 1
        assert OfferEntry.objects.filter(offer=offer).count() == 1

    def test_valid_multiple_offers_creates_multiple_entries(self, offer):
        details = {
            "offers": [
                {"id": "fe-1", "item": "Item 1", "cost": 100, "clientCost": 120},
                {"id": "fe-2", "item": "Item 2", "cost": 200, "clientCost": 240},
                {"id": "fe-3", "item": "Item 3", "cost": 300, "clientCost": 360},
            ],
        }
        count = parse_offer_details_to_entries(offer, details)
        assert count == 3
        assert OfferEntry.objects.filter(offer=offer).count() == 3

    def test_maps_all_fields_correctly(self, offer):
        details = {
            "offers": [
                {
                    "id": "frontend-uuid-1",
                    "item": "Gaffer",
                    "price": 1500,
                    "cost": 1200,
                    "clientPrice": 1800,
                    "clientCost": 1600,
                    "surcharge": 20,
                    "taxRate": 6,
                    "taxPrice": 108,
                    "showTax": True,
                    "overtime": 50,
                    "isLinkedSurcharge": False,
                    "marketRange": "high",
                },
            ],
        }
        parse_offer_details_to_entries(offer, details)
        entry_record = OfferEntry.objects.get(offer=offer)
        assert entry_record.frontend_id == "frontend-uuid-1"
        assert entry_record.item_name == "Gaffer"
        assert entry_record.price == Decimal("1500")
        assert entry_record.cost == Decimal("1200")
        assert entry_record.client_price == Decimal("1800")
        assert entry_record.client_cost == Decimal("1600")
        assert entry_record.surcharge == Decimal("20")
        assert entry_record.tax_rate == Decimal("6")
        assert entry_record.tax_price == Decimal("108")
        assert entry_record.show_tax is True
        assert entry_record.overtime == Decimal("50")
        assert entry_record.is_linked_surcharge is False
        assert entry_record.market_range == "high"
        assert entry_record.sort_order == 0

    def test_extra_keys_stored_in_item_data(self, offer):
        details = {
            "offers": [
                {
                    "id": "fe-1",
                    "item": "Item",
                    "units": [{"type": "quantity", "count": 1}],
                    "options": {"time": []},
                    "customField": "custom-value",
                },
            ],
        }
        parse_offer_details_to_entries(offer, details)
        entry_record = OfferEntry.objects.get(offer=offer)
        assert entry_record.item_data["units"] == [{"type": "quantity", "count": 1}]
        assert entry_record.item_data["options"] == {"time": []}
        assert entry_record.item_data["customField"] == "custom-value"
        assert "id" not in entry_record.item_data
        assert "item" not in entry_record.item_data

    def test_replaces_existing_entries_full_sync(self, offer):
        OfferEntry.objects.create(
            offer=offer,
            frontend_id="old-1",
            item_name="Old Item",
            sort_order=0,
        )
        OfferEntry.objects.create(
            offer=offer,
            frontend_id="old-2",
            item_name="Old Item 2",
            sort_order=1,
        )
        assert OfferEntry.objects.filter(offer=offer).count() == 2

        details = {
            "offers": [
                {"id": "new-1", "item": "New Item"},
            ],
        }
        count = parse_offer_details_to_entries(offer, details)
        assert count == 1
        assert OfferEntry.objects.filter(offer=offer).count() == 1
        assert OfferEntry.objects.get(offer=offer).frontend_id == "new-1"

    def test_catalog_entry_linked_when_found(self, offer, entry):
        details = {
            "offers": [
                {
                    "id": "fe-1",
                    "item": "Camera Operator",
                    "entryId": str(entry.id),
                },
            ],
        }
        parse_offer_details_to_entries(offer, details)
        entry_record = OfferEntry.objects.get(offer=offer)
        assert entry_record.entry == entry

    def test_catalog_category_linked_when_found(self, offer, category):
        details = {
            "offers": [
                {
                    "id": "fe-1",
                    "item": "Item",
                    "categoryId": str(category.id),
                },
            ],
        }
        parse_offer_details_to_entries(offer, details)
        entry_record = OfferEntry.objects.get(offer=offer)
        assert entry_record.category == category

    def test_missing_catalog_entry_null(self, offer):
        details = {
            "offers": [
                {
                    "id": "fe-1",
                    "item": "Item",
                    "entryId": str(uuid.uuid4()),
                },
            ],
        }
        parse_offer_details_to_entries(offer, details)
        entry_record = OfferEntry.objects.get(offer=offer)
        assert entry_record.entry is None

    def test_missing_catalog_category_null(self, offer):
        details = {
            "offers": [
                {
                    "id": "fe-1",
                    "item": "Item",
                    "categoryId": str(uuid.uuid4()),
                },
            ],
        }
        parse_offer_details_to_entries(offer, details)
        entry_record = OfferEntry.objects.get(offer=offer)
        assert entry_record.category is None

    def test_saves_metadata_and_details_on_offer(self, offer):
        details = {
            "offers": [{"id": "fe-1", "item": "Item"}],
            "surchargePercent": 20,
            "taxRate": 6,
            "someSetting": True,
        }
        parse_offer_details_to_entries(offer, details)
        offer.refresh_from_db()
        assert offer.details == details
        assert offer.metadata["surchargePercent"] == 20
        assert offer.metadata["taxRate"] == 6
        assert offer.metadata["someSetting"] is True
        assert "offers" not in offer.metadata

    def test_non_dict_items_in_offers_list_skipped(self, offer):
        details = {
            "offers": [
                {"id": "fe-1", "item": "Valid Item"},
                "not-a-dict",
                42,
                None,
                {"id": "fe-2", "item": "Another Valid"},
            ],
        }
        count = parse_offer_details_to_entries(offer, details)
        assert count == 2
        assert OfferEntry.objects.filter(offer=offer).count() == 2

    def test_sort_order_preserved(self, offer):
        details = {
            "offers": [
                {"id": "fe-0", "item": "First"},
                {"id": "fe-1", "item": "Second"},
                {"id": "fe-2", "item": "Third"},
            ],
        }
        parse_offer_details_to_entries(offer, details)
        entries = list(OfferEntry.objects.filter(offer=offer).order_by("sort_order"))
        assert entries[0].sort_order == 0
        assert entries[0].frontend_id == "fe-0"
        assert entries[1].sort_order == 1
        assert entries[1].frontend_id == "fe-1"
        assert entries[2].sort_order == 2
        assert entries[2].frontend_id == "fe-2"

    def test_empty_offers_list_creates_zero_entries(self, offer):
        details: dict = {"offers": []}
        count = parse_offer_details_to_entries(offer, details)
        assert count == 0
        assert OfferEntry.objects.filter(offer=offer).count() == 0


class TestReconstructDetailsFromEntries:
    def test_no_entries_returns_offer_details_fallback(self, offer):
        offer.details = {"key": "value", "offers": []}
        offer.save(update_fields=["details"])
        result = reconstruct_details_from_entries(offer)
        assert result == {"key": "value", "offers": []}

    def test_single_entry_reconstructed_correctly(self, offer):
        OfferEntry.objects.create(
            offer=offer,
            frontend_id="fe-1",
            item_name="Camera Operator",
            price=Decimal("500.00"),
            cost=Decimal("400.00"),
            client_price=Decimal("600.00"),
            client_cost=Decimal("550.00"),
            surcharge=Decimal("10.00"),
            tax_rate=Decimal("6.00"),
            tax_price=Decimal("33.00"),
            show_tax=False,
            overtime=Decimal("0"),
            is_linked_surcharge=True,
            market_range="mid",
            sort_order=0,
        )
        result = reconstruct_details_from_entries(offer)
        assert "offers" in result
        assert len(result["offers"]) == 1
        item = result["offers"][0]
        assert item["id"] == "fe-1"
        assert item["item"] == "Camera Operator"
        assert item["price"] == 500.0
        assert item["cost"] == 400.0
        assert item["clientPrice"] == 600.0
        assert item["clientCost"] == 550.0
        assert item["surcharge"] == 10.0
        assert item["taxRate"] == 6.0
        assert item["taxPrice"] == 33.0
        assert item["showTax"] is False
        assert item["overtime"] == 0.0
        assert item["isLinkedSurcharge"] is True
        assert item["marketRange"] == "mid"

    def test_multiple_entries_in_sort_order(self, offer):
        OfferEntry.objects.create(
            offer=offer,
            frontend_id="fe-2",
            item_name="Second",
            sort_order=1,
        )
        OfferEntry.objects.create(
            offer=offer,
            frontend_id="fe-1",
            item_name="First",
            sort_order=0,
        )
        result = reconstruct_details_from_entries(offer)
        assert result["offers"][0]["id"] == "fe-1"
        assert result["offers"][1]["id"] == "fe-2"

    def test_item_data_fields_restored(self, offer):
        OfferEntry.objects.create(
            offer=offer,
            frontend_id="fe-1",
            item_name="Item",
            sort_order=0,
            item_data={
                "units": [{"type": "quantity", "count": 2}],
                "options": {"time": [{"label": "Hour"}]},
                "customField": "custom",
            },
        )
        result = reconstruct_details_from_entries(offer)
        item = result["offers"][0]
        assert item["units"] == [{"type": "quantity", "count": 2}]
        assert item["options"] == {"time": [{"label": "Hour"}]}
        assert item["customField"] == "custom"

    def test_structured_fields_override_item_data(self, offer):
        OfferEntry.objects.create(
            offer=offer,
            frontend_id="fe-1",
            item_name="Correct Name",
            price=Decimal("999.00"),
            sort_order=0,
            item_data={
                "id": "stale-id",
                "item": "Stale Name",
                "price": 0,
            },
        )
        result = reconstruct_details_from_entries(offer)
        item = result["offers"][0]
        assert item["id"] == "fe-1"
        assert item["item"] == "Correct Name"
        assert item["price"] == 999.0

    def test_metadata_merged_into_result(self, offer):
        offer.metadata = {
            "surchargePercent": 20,
            "taxRate": 6,
            "categories": [{"id": "cat-1"}],
        }
        offer.save(update_fields=["metadata"])
        OfferEntry.objects.create(
            offer=offer,
            frontend_id="fe-1",
            item_name="Item",
            sort_order=0,
        )
        result = reconstruct_details_from_entries(offer)
        assert result["surchargePercent"] == 20
        assert result["taxRate"] == 6
        assert result["categories"] == [{"id": "cat-1"}]
        assert "offers" in result

    def test_null_fields_handled(self, offer):
        OfferEntry.objects.create(
            offer=offer,
            frontend_id="fe-1",
            item_name="Item",
            price=None,
            cost=None,
            client_price=None,
            client_cost=None,
            surcharge=None,
            tax_price=None,
            sort_order=0,
        )
        result = reconstruct_details_from_entries(offer)
        item = result["offers"][0]
        assert "price" not in item
        assert "cost" not in item
        assert "clientPrice" not in item
        assert "clientCost" not in item
        assert "surcharge" not in item
        assert "taxPrice" not in item
        assert item["taxRate"] == 0.0
        assert item["overtime"] == 0.0

    def test_entry_id_and_category_id_included_when_present(
        self, offer, entry, category
    ):
        OfferEntry.objects.create(
            offer=offer,
            frontend_id="fe-1",
            item_name="Linked Item",
            entry=entry,
            category=category,
            sort_order=0,
        )
        result = reconstruct_details_from_entries(offer)
        item = result["offers"][0]
        assert item["entryId"] == str(entry.id)
        assert item["categoryId"] == str(category.id)

    def test_entry_id_and_category_id_absent_when_null(self, offer):
        OfferEntry.objects.create(
            offer=offer,
            frontend_id="fe-1",
            item_name="Unlinked Item",
            entry=None,
            category=None,
            sort_order=0,
        )
        result = reconstruct_details_from_entries(offer)
        item = result["offers"][0]
        assert "entryId" not in item
        assert "categoryId" not in item

    def test_empty_metadata_handled(self, offer):
        offer.metadata = {}
        offer.save(update_fields=["metadata"])
        OfferEntry.objects.create(
            offer=offer,
            frontend_id="fe-1",
            item_name="Item",
            sort_order=0,
        )
        result = reconstruct_details_from_entries(offer)
        assert "offers" in result
        assert len(result["offers"]) == 1

    def test_non_dict_metadata_handled(self, offer):
        offer.metadata = "not-a-dict"
        offer.save(update_fields=["metadata"])
        OfferEntry.objects.create(
            offer=offer,
            frontend_id="fe-1",
            item_name="Item",
            sort_order=0,
        )
        result = reconstruct_details_from_entries(offer)
        assert "offers" in result
        assert len(result["offers"]) == 1

    def test_roundtrip_parse_then_reconstruct(self, offer, entry, category):
        details = {
            "offers": [
                {
                    "id": "fe-1",
                    "item": "Camera Operator",
                    "entryId": str(entry.id),
                    "categoryId": str(category.id),
                    "price": 1500,
                    "cost": 1200,
                    "clientPrice": 1800,
                    "clientCost": 1600,
                    "surcharge": 20,
                    "taxRate": 6,
                    "taxPrice": 108,
                    "showTax": False,
                    "overtime": 0,
                    "isLinkedSurcharge": True,
                    "marketRange": "mid",
                    "units": [{"type": "quantity", "count": 1}],
                },
            ],
            "surchargePercent": 20,
            "taxRate": 6,
        }
        parse_offer_details_to_entries(offer, details)
        offer.refresh_from_db()
        result = reconstruct_details_from_entries(offer)
        reconstructed_item = result["offers"][0]
        assert reconstructed_item["id"] == "fe-1"
        assert reconstructed_item["item"] == "Camera Operator"
        assert reconstructed_item["entryId"] == str(entry.id)
        assert reconstructed_item["categoryId"] == str(category.id)
        assert reconstructed_item["price"] == 1500.0
        assert reconstructed_item["cost"] == 1200.0
        assert reconstructed_item["clientPrice"] == 1800.0
        assert reconstructed_item["clientCost"] == 1600.0
        assert reconstructed_item["surcharge"] == 20.0
        assert reconstructed_item["taxRate"] == 6.0
        assert reconstructed_item["taxPrice"] == 108.0
        assert reconstructed_item["showTax"] is False
        assert reconstructed_item["overtime"] == 0.0
        assert reconstructed_item["isLinkedSurcharge"] is True
        assert reconstructed_item["marketRange"] == "mid"
        assert reconstructed_item["units"] == [{"type": "quantity", "count": 1}]
        assert result["surchargePercent"] == 20
        assert result["taxRate"] == 6
