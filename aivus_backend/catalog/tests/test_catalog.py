import json
import uuid

import pytest
from django.conf import settings
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.test import Client as DjangoTestClient
from django.utils import timezone

from aivus_backend.catalog.models import Category
from aivus_backend.catalog.models import Entry
from aivus_backend.catalog.models import EntryUnit
from aivus_backend.catalog.models import Unit
from aivus_backend.users.models import User
from aivus_backend.users.models import Vendor


def _auth_headers(user, group=None):
    return {
        "HTTP_X_API_KEY": settings.API_KEY,
        "HTTP_X_USER_ID": str(user.id),
        "HTTP_X_USER_GROUP": group or user.group,
    }


def _vendor_headers(user, vendor_id=None):
    headers = _auth_headers(user, "VENDOR")
    if vendor_id:
        headers["HTTP_X_VENDOR_ID"] = str(vendor_id)
    return headers


@pytest.fixture
def api_client():
    return DjangoTestClient()


@pytest.fixture
def vendor_user(db):
    return User.objects.create_user(
        email="catalog-vendor@example.com",
        password="testpass123",
        name="Catalog Test Vendor",
        group="VENDOR",
    )


@pytest.fixture
def client_user(db):
    return User.objects.create_user(
        email="catalog-client@example.com",
        password="testpass123",
        name="Catalog Test Client",
        group="CLIENT",
    )


@pytest.fixture
def vendor(vendor_user):
    return Vendor.objects.create(name="Catalog Test Agency", owner=vendor_user)


@pytest.fixture
def parent_category(db):
    return Category.objects.create(
        name="Production",
        code="PR",
        level=0,
        tags=["production"],
    )


@pytest.fixture
def child_category(parent_category):
    return Category.objects.create(
        name="Camera",
        code="CM",
        level=1,
        parent_category=parent_category,
    )


@pytest.fixture
def grandchild_category(child_category):
    return Category.objects.create(
        name="Lenses",
        level=2,
        parent_category=child_category,
    )


@pytest.fixture
def unit_quantity(db):
    return Unit.objects.create(
        name="Piece",
        symbol="pc",
        dimension="QUANTITY",
        is_default=True,
    )


@pytest.fixture
def unit_quantity_second(db):
    return Unit.objects.create(
        name="Flat",
        symbol="flat",
        dimension="QUANTITY",
        is_default=False,
    )


@pytest.fixture
def unit_temporal(db):
    return Unit.objects.create(
        name="Day",
        symbol="day",
        dimension="TEMPORAL",
        is_default=True,
    )


@pytest.fixture
def entry(parent_category):
    return Entry.objects.create(
        name="Camera Operator",
        code="CO",
        short_description="Professional camera operator",
        description="Full description of camera operator services",
        is_approved=True,
        category=parent_category,
    )


@pytest.fixture
def unapproved_entry(parent_category):
    return Entry.objects.create(
        name="Unapproved Entry",
        is_approved=False,
        category=parent_category,
    )


@pytest.fixture
def entry_unit(entry, unit_quantity):
    return EntryUnit.objects.create(
        entry=entry,
        unit=unit_quantity,
        is_default=True,
    )


@pytest.fixture
def entry_unit_temporal(entry, unit_temporal):
    return EntryUnit.objects.create(
        entry=entry,
        unit=unit_temporal,
        is_default=False,
    )


class TestCategoryModel:
    def test_create_category(self, parent_category):
        assert parent_category.name == "Production"
        assert parent_category.code == "PR"
        assert parent_category.level == 0
        assert parent_category.tags == ["production"]
        assert parent_category.deleted_at is None

    def test_str_with_code(self, parent_category):
        assert str(parent_category) == "[PR] Production"

    def test_str_without_code(self, db):
        category = Category.objects.create(name="Misc", level=0)
        assert str(category) == "Misc"

    def test_get_full_path_single_level(self, parent_category):
        assert parent_category.get_full_path() == "Production"

    def test_get_full_path_two_levels(self, child_category):
        assert child_category.get_full_path() == "Production > Camera"

    def test_get_full_path_three_levels(self, grandchild_category):
        assert grandchild_category.get_full_path() == "Production > Camera > Lenses"

    def test_soft_delete_filtering(self, parent_category):
        parent_category.deleted_at = timezone.now()
        parent_category.save()
        active = Category.objects.filter(deleted_at__isnull=True)
        assert parent_category not in active

    def test_parent_child_relationship(self, parent_category, child_category):
        assert child_category.parent_category == parent_category
        assert child_category in parent_category.children.all()

    def test_set_null_on_parent_delete(self, parent_category, child_category):
        parent_category.delete()
        child_category.refresh_from_db()
        assert child_category.parent_category is None


class TestUnitModel:
    def test_create_unit(self, unit_quantity):
        assert unit_quantity.name == "Piece"
        assert unit_quantity.symbol == "pc"
        assert unit_quantity.dimension == "QUANTITY"
        assert unit_quantity.is_default is True
        assert unit_quantity.deleted_at is None

    def test_str_regular_unit(self, unit_temporal):
        assert str(unit_temporal) == "Day (s)"

    def test_str_flat_unit(self, db):
        unit = Unit.objects.create(
            name="Flat",
            symbol="flat",
            dimension="QUANTITY",
        )
        assert str(unit) == "Flat"

    def test_str_each_unit(self, db):
        unit = Unit.objects.create(
            name="Each",
            symbol="ea",
            dimension="QUANTITY",
        )
        assert str(unit) == "Each"

    def test_is_default_exclusivity_within_dimension(self, db):
        first = Unit.objects.create(
            name="Day",
            symbol="day",
            dimension="TEMPORAL",
            is_default=True,
        )
        second = Unit.objects.create(
            name="Hour",
            symbol="hr",
            dimension="TEMPORAL",
            is_default=True,
        )
        first.refresh_from_db()
        assert first.is_default is False
        assert second.is_default is True

    def test_is_default_does_not_affect_other_dimension(self, db):
        quantity_unit = Unit.objects.create(
            name="Piece",
            symbol="pc",
            dimension="QUANTITY",
            is_default=True,
        )
        Unit.objects.create(
            name="Day",
            symbol="day",
            dimension="TEMPORAL",
            is_default=True,
        )
        quantity_unit.refresh_from_db()
        assert quantity_unit.is_default is True

    def test_soft_delete(self, unit_quantity):
        unit_quantity.deleted_at = timezone.now()
        unit_quantity.save()
        active = Unit.objects.filter(deleted_at__isnull=True)
        assert unit_quantity not in active


class TestEntryModel:
    def test_create_entry(self, entry):
        assert entry.name == "Camera Operator"
        assert entry.code == "CO"
        assert entry.short_description == "Professional camera operator"
        assert entry.is_approved is True
        assert entry.deleted_at is None

    def test_str(self, entry):
        assert str(entry) == "Camera Operator"

    def test_protect_on_category_delete(self, entry):
        with pytest.raises(ProtectedError):
            entry.category.delete()

    def test_is_approved_default_false(self, parent_category):
        new_entry = Entry.objects.create(
            name="New Entry",
            category=parent_category,
        )
        assert new_entry.is_approved is False

    def test_soft_delete(self, entry):
        entry.deleted_at = timezone.now()
        entry.save()
        active = Entry.objects.filter(deleted_at__isnull=True)
        assert entry not in active


class TestEntryUnitModel:
    def test_create_entry_unit(self, entry_unit, entry, unit_quantity):
        assert entry_unit.entry == entry
        assert entry_unit.unit == unit_quantity
        assert entry_unit.is_default is True

    def test_str_default(self, entry_unit):
        assert str(entry_unit) == "Camera Operator - pc (default)"

    def test_str_non_default(self, entry_unit_temporal):
        assert str(entry_unit_temporal) == "Camera Operator - day"

    def test_is_default_exclusivity_per_entry(
        self, entry, unit_quantity, unit_temporal
    ):
        first = EntryUnit.objects.create(
            entry=entry,
            unit=unit_quantity,
            is_default=True,
        )
        second = EntryUnit.objects.create(
            entry=entry,
            unit=unit_temporal,
            is_default=True,
        )
        first.refresh_from_db()
        assert first.is_default is False
        assert second.is_default is True

    def test_unique_together_constraint(self, entry, unit_quantity):
        EntryUnit.objects.create(entry=entry, unit=unit_quantity)
        with pytest.raises(IntegrityError):
            EntryUnit.objects.create(entry=entry, unit=unit_quantity)

    def test_cascade_on_entry_delete(self, entry_unit, entry):
        entry_id = entry_unit.id
        entry.delete()
        assert not EntryUnit.objects.filter(id=entry_id).exists()

    def test_cascade_on_unit_delete(self, entry_unit, unit_quantity):
        entry_unit_id = entry_unit.id
        unit_quantity.delete()
        assert not EntryUnit.objects.filter(id=entry_unit_id).exists()


class TestGetCategories:
    def test_returns_all_non_deleted(
        self, api_client, vendor_user, vendor, parent_category, child_category
    ):
        headers = _vendor_headers(vendor_user, vendor.id)
        response = api_client.get("/api/v1/categories", **headers)
        assert response.status_code == 200
        data = json.loads(response.content)
        ids = [x["id"] for x in data]
        assert str(parent_category.id) in ids
        assert str(child_category.id) in ids

    def test_excludes_soft_deleted(
        self, api_client, vendor_user, vendor, parent_category
    ):
        parent_category.deleted_at = timezone.now()
        parent_category.save()
        headers = _vendor_headers(vendor_user, vendor.id)
        response = api_client.get("/api/v1/categories", **headers)
        assert response.status_code == 200
        data = json.loads(response.content)
        ids = [x["id"] for x in data]
        assert str(parent_category.id) not in ids

    def test_requires_auth(self, api_client):
        response = api_client.get("/api/v1/categories")
        assert response.status_code in (401, 403)

    def test_wrong_group_denied(self, api_client, db):
        unconfirmed = User.objects.create_user(
            email="cat-unconfirmed@example.com",
            password="testpass123",
            name="Unconfirmed",
            group="UNCONFIRMED",
        )
        headers = _auth_headers(unconfirmed, "UNCONFIRMED")
        response = api_client.get("/api/v1/categories", **headers)
        assert response.status_code == 403

    def test_client_group_allowed(self, api_client, client_user, parent_category):
        headers = _auth_headers(client_user, "CLIENT")
        response = api_client.get("/api/v1/categories", **headers)
        assert response.status_code == 200

    def test_serialization_format(
        self, api_client, vendor_user, vendor, parent_category, child_category
    ):
        headers = _vendor_headers(vendor_user, vendor.id)
        response = api_client.get("/api/v1/categories", **headers)
        data = json.loads(response.content)
        parent_data = next(x for x in data if x["id"] == str(parent_category.id))
        assert parent_data["name"] == "Production"
        assert parent_data["level"] == 0
        assert parent_data["parentCategoryId"] is None
        assert parent_data["tags"] == ["production"]

        child_data = next(x for x in data if x["id"] == str(child_category.id))
        assert child_data["parentCategoryId"] == str(parent_category.id)


class TestGetEntries:
    def test_returns_only_approved_non_deleted(
        self, api_client, vendor_user, vendor, entry, unapproved_entry
    ):
        headers = _vendor_headers(vendor_user, vendor.id)
        response = api_client.get("/api/v1/entries", **headers)
        assert response.status_code == 200
        data = json.loads(response.content)
        ids = [x["id"] for x in data["entries"]]
        assert str(entry.id) in ids
        assert str(unapproved_entry.id) not in ids

    def test_excludes_soft_deleted(self, api_client, vendor_user, vendor, entry):
        entry.deleted_at = timezone.now()
        entry.save()
        headers = _vendor_headers(vendor_user, vendor.id)
        response = api_client.get("/api/v1/entries", **headers)
        data = json.loads(response.content)
        ids = [x["id"] for x in data["entries"]]
        assert str(entry.id) not in ids

    def test_full_true_includes_units(
        self, api_client, vendor_user, vendor, entry, entry_unit, entry_unit_temporal
    ):
        headers = _vendor_headers(vendor_user, vendor.id)
        response = api_client.get("/api/v1/entries?full=true", **headers)
        data = json.loads(response.content)
        entry_data = next(x for x in data["entries"] if x["id"] == str(entry.id))
        assert "units" in entry_data
        assert "description" in entry_data
        assert "quantity" in entry_data["units"]
        assert "temporal" in entry_data["units"]

    def test_full_false_excludes_units(
        self, api_client, vendor_user, vendor, entry, entry_unit
    ):
        headers = _vendor_headers(vendor_user, vendor.id)
        response = api_client.get("/api/v1/entries?full=false", **headers)
        data = json.loads(response.content)
        entry_data = next(x for x in data["entries"] if x["id"] == str(entry.id))
        assert "units" not in entry_data
        assert "description" not in entry_data

    def test_default_excludes_units(
        self, api_client, vendor_user, vendor, entry, entry_unit
    ):
        headers = _vendor_headers(vendor_user, vendor.id)
        response = api_client.get("/api/v1/entries", **headers)
        data = json.loads(response.content)
        entry_data = next(x for x in data["entries"] if x["id"] == str(entry.id))
        assert "units" not in entry_data

    def test_requires_auth(self, api_client):
        response = api_client.get("/api/v1/entries")
        assert response.status_code in (401, 403)

    def test_serialization_format(self, api_client, vendor_user, vendor, entry):
        headers = _vendor_headers(vendor_user, vendor.id)
        response = api_client.get("/api/v1/entries", **headers)
        data = json.loads(response.content)
        entry_data = next(x for x in data["entries"] if x["id"] == str(entry.id))
        assert entry_data["name"] == "Camera Operator"
        assert entry_data["shortDescription"] == "Professional camera operator"
        assert entry_data["categoryId"] == str(entry.category_id)


class TestGetEntry:
    def test_returns_entry_by_id(self, api_client, vendor_user, vendor, entry):
        headers = _vendor_headers(vendor_user, vendor.id)
        response = api_client.get(f"/api/v1/entries/{entry.id}", **headers)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["id"] == str(entry.id)
        assert data["name"] == "Camera Operator"

    def test_404_for_nonexistent(self, api_client, vendor_user, vendor):
        headers = _vendor_headers(vendor_user, vendor.id)
        fake_id = uuid.uuid4()
        response = api_client.get(f"/api/v1/entries/{fake_id}", **headers)
        assert response.status_code == 404

    def test_404_for_soft_deleted(self, api_client, vendor_user, vendor, entry):
        entry.deleted_at = timezone.now()
        entry.save()
        headers = _vendor_headers(vendor_user, vendor.id)
        response = api_client.get(f"/api/v1/entries/{entry.id}", **headers)
        assert response.status_code == 404

    def test_includes_units(
        self, api_client, vendor_user, vendor, entry, entry_unit, entry_unit_temporal
    ):
        headers = _vendor_headers(vendor_user, vendor.id)
        response = api_client.get(f"/api/v1/entries/{entry.id}", **headers)
        data = json.loads(response.content)
        assert "units" in data
        assert "description" in data
        assert len(data["units"]["quantity"]) >= 1
        assert len(data["units"]["temporal"]) >= 1

    def test_requires_auth(self, api_client, entry):
        response = api_client.get(f"/api/v1/entries/{entry.id}")
        assert response.status_code in (401, 403)

    def test_unit_serialization_in_entry(
        self, api_client, vendor_user, vendor, entry, entry_unit
    ):
        headers = _vendor_headers(vendor_user, vendor.id)
        response = api_client.get(f"/api/v1/entries/{entry.id}", **headers)
        data = json.loads(response.content)
        quantity_unit = data["units"]["quantity"][0]
        assert quantity_unit["id"] == str(entry_unit.unit.id)
        assert quantity_unit["name"] == "Piece"
        assert quantity_unit["symbol"] == "pc"
        assert quantity_unit["dimension"] == "QUANTITY"
        assert quantity_unit["isDefault"] is True


class TestGetUnits:
    def test_returns_all_non_deleted(
        self, api_client, vendor_user, vendor, unit_quantity, unit_temporal
    ):
        headers = _vendor_headers(vendor_user, vendor.id)
        response = api_client.get("/api/v1/units", **headers)
        assert response.status_code == 200
        data = json.loads(response.content)
        ids = [x["id"] for x in data["units"]]
        assert str(unit_quantity.id) in ids
        assert str(unit_temporal.id) in ids

    def test_excludes_soft_deleted(
        self, api_client, vendor_user, vendor, unit_quantity
    ):
        unit_quantity.deleted_at = timezone.now()
        unit_quantity.save()
        headers = _vendor_headers(vendor_user, vendor.id)
        response = api_client.get("/api/v1/units", **headers)
        data = json.loads(response.content)
        ids = [x["id"] for x in data["units"]]
        assert str(unit_quantity.id) not in ids

    def test_requires_auth(self, api_client):
        response = api_client.get("/api/v1/units")
        assert response.status_code in (401, 403)

    def test_wrong_group_denied(self, api_client, db):
        unconfirmed = User.objects.create_user(
            email="unit-unconfirmed@example.com",
            password="testpass123",
            name="Unconfirmed",
            group="UNCONFIRMED",
        )
        headers = _auth_headers(unconfirmed, "UNCONFIRMED")
        response = api_client.get("/api/v1/units", **headers)
        assert response.status_code == 403

    def test_client_group_allowed(self, api_client, client_user, unit_quantity):
        headers = _auth_headers(client_user, "CLIENT")
        response = api_client.get("/api/v1/units", **headers)
        assert response.status_code == 200

    def test_serialization_format(self, api_client, vendor_user, vendor, unit_quantity):
        headers = _vendor_headers(vendor_user, vendor.id)
        response = api_client.get("/api/v1/units", **headers)
        data = json.loads(response.content)
        unit_data = next(x for x in data["units"] if x["id"] == str(unit_quantity.id))
        assert unit_data["name"] == "Piece"
        assert unit_data["symbol"] == "pc"
        assert unit_data["dimension"] == "QUANTITY"
        assert unit_data["isDefault"] is True
