"""Tests for the PreVendor model and API."""

import json

import pytest
from django.conf import settings
from django.test import Client as DjangoTestClient
from django.utils import timezone

from aivus_backend.users.models import User
from aivus_backend.vendors.models import PreVendor


def _auth_headers(user, group=None):
    return {
        "HTTP_X_API_KEY": settings.API_KEY,
        "HTTP_X_USER_ID": str(user.id),
        "HTTP_X_USER_GROUP": group or user.group,
    }


@pytest.fixture
def api_client():
    return DjangoTestClient()


@pytest.fixture
def client_user(db):
    return User.objects.create_user(
        email="pre-vendor-client@example.com",
        password="testpass123",
        name="Pre-Vendor Test Client",
        group="CLIENT",
    )


@pytest.fixture
def pre_vendor_ru_first(db):
    return PreVendor.objects.create(
        title="Великий продакшен",
        short_description="Лидер индустрии.",
        language="ru",
        email="ru-1@example.com",
        sort_order=1,
        rank_label="TOP 1",
        category_label="Видеопродакшен",
        portfolio_url="https://example.com/portfolio-1",
        address="Москва",
    )


@pytest.fixture
def pre_vendor_ru_second(db):
    return PreVendor.objects.create(
        title="Второй продакшен",
        short_description="Уверенный середняк.",
        language="ru",
        email="ru-2@example.com",
        sort_order=2,
        rank_label="TOP 2",
        category_label="Видеопродакшен",
    )


@pytest.fixture
def pre_vendor_en(db):
    return PreVendor.objects.create(
        title="Great Studio",
        short_description="Industry leader.",
        language="en",
        email="en-1@example.com",
        sort_order=1,
        rank_label="TOP 1",
        category_label="Video Production",
    )


class TestPreVendorModel:
    def test_create(self, pre_vendor_ru_first):
        assert pre_vendor_ru_first.title == "Великий продакшен"
        assert pre_vendor_ru_first.language == "ru"
        assert pre_vendor_ru_first.sort_order == 1
        assert pre_vendor_ru_first.deleted_at is None

    def test_str(self, pre_vendor_ru_first):
        assert str(pre_vendor_ru_first) == "[ru] Великий продакшен"

    def test_default_ordering(
        self,
        pre_vendor_ru_second,
        pre_vendor_ru_first,
    ):
        ids = list(
            PreVendor.objects.filter(language="ru").values_list("id", flat=True),
        )
        assert ids[0] == pre_vendor_ru_first.id
        assert ids[1] == pre_vendor_ru_second.id

    def test_soft_delete(self, pre_vendor_ru_first):
        pre_vendor_ru_first.deleted_at = timezone.now()
        pre_vendor_ru_first.save()
        active = PreVendor.objects.filter(deleted_at__isnull=True)
        assert pre_vendor_ru_first not in active


class TestListPreVendors:
    def test_requires_auth(self, api_client):
        response = api_client.get("/api/v1/pre-vendors?language=ru")
        assert response.status_code in (401, 403)

    def test_returns_filtered_by_language(
        self,
        api_client,
        client_user,
        pre_vendor_ru_first,
        pre_vendor_en,
    ):
        headers = _auth_headers(client_user, "CLIENT")
        response = api_client.get("/api/v1/pre-vendors?language=ru", **headers)
        assert response.status_code == 200

        data = json.loads(response.content)
        ids = [x["id"] for x in data["preVendors"]]
        assert str(pre_vendor_ru_first.id) in ids
        assert str(pre_vendor_en.id) not in ids

    def test_orders_by_sort_order(
        self,
        api_client,
        client_user,
        pre_vendor_ru_first,
        pre_vendor_ru_second,
    ):
        headers = _auth_headers(client_user, "CLIENT")
        response = api_client.get("/api/v1/pre-vendors?language=ru", **headers)
        data = json.loads(response.content)
        ids = [x["id"] for x in data["preVendors"]]
        assert ids == [str(pre_vendor_ru_first.id), str(pre_vendor_ru_second.id)]

    def test_excludes_soft_deleted(
        self,
        api_client,
        client_user,
        pre_vendor_ru_first,
    ):
        pre_vendor_ru_first.deleted_at = timezone.now()
        pre_vendor_ru_first.save()
        headers = _auth_headers(client_user, "CLIENT")
        response = api_client.get("/api/v1/pre-vendors?language=ru", **headers)
        data = json.loads(response.content)
        ids = [x["id"] for x in data["preVendors"]]
        assert str(pre_vendor_ru_first.id) not in ids

    def test_invalid_language_returns_400(self, api_client, client_user):
        headers = _auth_headers(client_user, "CLIENT")
        response = api_client.get("/api/v1/pre-vendors?language=de", **headers)
        assert response.status_code == 400

    def test_default_language_is_en(
        self,
        api_client,
        client_user,
        pre_vendor_en,
    ):
        headers = _auth_headers(client_user, "CLIENT")
        response = api_client.get("/api/v1/pre-vendors", **headers)
        data = json.loads(response.content)
        ids = [x["id"] for x in data["preVendors"]]
        assert str(pre_vendor_en.id) in ids

    def test_serialization_format(
        self,
        api_client,
        client_user,
        pre_vendor_ru_first,
    ):
        headers = _auth_headers(client_user, "CLIENT")
        response = api_client.get("/api/v1/pre-vendors?language=ru", **headers)
        data = json.loads(response.content)
        item = data["preVendors"][0]
        assert item["id"] == str(pre_vendor_ru_first.id)
        assert item["title"] == "Великий продакшен"
        assert item["shortDescription"] == "Лидер индустрии."
        assert item["portfolioUrl"] == "https://example.com/portfolio-1"
        assert item["address"] == "Москва"
        assert item["email"] == "ru-1@example.com"
        assert item["language"] == "ru"
        assert item["rankLabel"] == "TOP 1"
        assert item["categoryLabel"] == "Видеопродакшен"
        assert item["sortOrder"] == 1
        assert item["logoUrl"] == ""
