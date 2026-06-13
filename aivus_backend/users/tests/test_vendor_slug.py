"""Tests for vendor brief-link slug and lead-notification settings (S2-2)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from django.conf import settings as django_settings
from django.test import Client as DjangoTestClient
from django.urls import reverse

from aivus_backend.core.slugs import is_reserved_slug
from aivus_backend.core.slugs import validate_slug
from aivus_backend.users.models import User
from aivus_backend.users.models import Vendor
from aivus_backend.users.models import VendorSettings


@pytest.fixture
def api_client() -> DjangoTestClient:
    return DjangoTestClient()


@pytest.fixture
def vendor_user(db):
    user = User.objects.create_user(
        email="vendor-slug@example.com",
        password="p@ssw0rd",
        name="Vendor Owner",
        group="VENDOR",
    )
    vendor = Vendor.objects.create(name="Pixel Forge Studio", owner=user)
    return user, vendor


def _auth(user) -> dict:
    return {
        "HTTP_X_API_KEY": django_settings.API_KEY,
        "HTTP_X_USER_ID": str(user.id),
        "HTTP_X_USER_GROUP": user.group,
    }


# --- validation unit tests ---------------------------------------------------


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("ab", False),
        ("abc", True),
        ("Pixel-Forge", False),
        ("pixel-forge", True),
        ("-leading", False),
        ("trailing-", False),
        ("double--hyphen", False),
        ("with space", False),
        ("a" * 41, False),
        ("a" * 40, True),
        ("brief", False),
        ("admin", False),
    ],
)
def test_validate_slug(value, valid):
    assert (validate_slug(value) is None) is valid


def test_reserved_slugs_include_frontend_segments():
    for name in ("brief", "auth", "public-brief", "shared-brief", "app", "settings"):
        assert is_reserved_slug(name)


# --- settings GET / lazy default ---------------------------------------------


@pytest.mark.django_db
def test_get_settings_returns_null_slug_without_llm_or_persist(api_client, vendor_user):
    user, vendor = vendor_user
    with patch("aivus_backend.users.slug_suggest._llm_candidate") as llm_mock:
        response = api_client.get(reverse("vendor-settings"), **_auth(user))

    assert response.status_code == 200
    assert response.json()["slug"] is None
    llm_mock.assert_not_called()
    assert VendorSettings.objects.get(vendor=vendor).slug is None


@pytest.mark.django_db
def test_get_settings_returns_persisted_slug(api_client, vendor_user):
    user, vendor = vendor_user
    VendorSettings.objects.create(vendor=vendor, slug="fixed-slug")
    with patch("aivus_backend.users.slug_suggest._llm_candidate") as llm_mock:
        response = api_client.get(reverse("vendor-settings"), **_auth(user))

    assert response.json()["slug"] == "fixed-slug"
    llm_mock.assert_not_called()


# --- settings PATCH ----------------------------------------------------------


@pytest.mark.django_db
def test_patch_sets_slug_and_email(api_client, vendor_user):
    user, vendor = vendor_user
    VendorSettings.objects.create(vendor=vendor)
    response = api_client.patch(
        reverse("vendor-settings"),
        data=json.dumps(
            {"slug": "my-studio", "leadNotificationEmail": "leads@studio.com"}
        ),
        content_type="application/json",
        **_auth(user),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["slug"] == "my-studio"
    assert body["leadNotificationEmail"] == "leads@studio.com"


@pytest.mark.django_db
def test_patch_rejects_reserved_slug(api_client, vendor_user):
    user, vendor = vendor_user
    VendorSettings.objects.create(vendor=vendor)
    response = api_client.patch(
        reverse("vendor-settings"),
        data=json.dumps({"slug": "brief"}),
        content_type="application/json",
        **_auth(user),
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_patch_rejects_invalid_slug(api_client, vendor_user):
    user, vendor = vendor_user
    VendorSettings.objects.create(vendor=vendor)
    response = api_client.patch(
        reverse("vendor-settings"),
        data=json.dumps({"slug": "Bad Slug!"}),
        content_type="application/json",
        **_auth(user),
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_patch_rejects_non_string_slug(api_client, vendor_user):
    """SF-7: a non-string slug (number) must 400, not silently wipe the slug."""
    user, vendor = vendor_user
    VendorSettings.objects.create(vendor=vendor, slug="keep-me")
    response = api_client.patch(
        reverse("vendor-settings"),
        data=json.dumps({"slug": 123}),
        content_type="application/json",
        **_auth(user),
    )
    assert response.status_code == 400
    assert VendorSettings.objects.get(vendor=vendor).slug == "keep-me"


@pytest.mark.django_db
def test_patch_null_slug_clears_it(api_client, vendor_user):
    """Explicit null still clears the slug (intentional opt-out)."""
    user, vendor = vendor_user
    VendorSettings.objects.create(vendor=vendor, slug="drop-me")
    response = api_client.patch(
        reverse("vendor-settings"),
        data=json.dumps({"slug": None}),
        content_type="application/json",
        **_auth(user),
    )
    assert response.status_code == 200
    assert response.json()["slug"] is None
    assert VendorSettings.objects.get(vendor=vendor).slug is None


@pytest.mark.django_db
def test_patch_collision_returns_409(api_client, vendor_user):
    user, vendor = vendor_user
    VendorSettings.objects.create(vendor=vendor)

    other_user = User.objects.create_user(
        email="other-vendor@example.com",
        password="p@ssw0rd",
        name="Other",
        group="VENDOR",
    )
    other_vendor = Vendor.objects.create(name="Other Studio", owner=other_user)
    VendorSettings.objects.create(vendor=other_vendor, slug="taken-slug")

    response = api_client.patch(
        reverse("vendor-settings"),
        data=json.dumps({"slug": "taken-slug"}),
        content_type="application/json",
        **_auth(user),
    )
    assert response.status_code == 409


@pytest.mark.django_db
def test_patch_rejects_invalid_email(api_client, vendor_user):
    user, vendor = vendor_user
    VendorSettings.objects.create(vendor=vendor)
    response = api_client.patch(
        reverse("vendor-settings"),
        data=json.dumps({"leadNotificationEmail": "not-an-email"}),
        content_type="application/json",
        **_auth(user),
    )
    assert response.status_code == 400


# --- slug suggest endpoint ---------------------------------------------------


@pytest.mark.django_db
def test_slug_suggest_endpoint(api_client, vendor_user):
    user, _vendor = vendor_user
    with patch(
        "aivus_backend.users.slug_suggest._llm_candidate", return_value="suggested-one"
    ):
        response = api_client.get(reverse("vendor-slug-suggest"), **_auth(user))

    assert response.status_code == 200
    assert response.json()["slug"] == "suggested-one"


# --- slug check endpoint -----------------------------------------------------


@pytest.mark.django_db
def test_slug_check_available(api_client, vendor_user):
    user, vendor = vendor_user
    VendorSettings.objects.create(vendor=vendor)
    response = api_client.get(
        reverse("vendor-slug-check"), {"slug": "totally-free"}, **_auth(user)
    )
    assert response.status_code == 200
    assert response.json() == {"available": True}


@pytest.mark.django_db
def test_slug_check_taken_by_other_vendor(api_client, vendor_user):
    user, vendor = vendor_user
    VendorSettings.objects.create(vendor=vendor)

    other_user = User.objects.create_user(
        email="holder@example.com",
        password="p@ssw0rd",
        name="Holder",
        group="VENDOR",
    )
    other_vendor = Vendor.objects.create(name="Holder Studio", owner=other_user)
    VendorSettings.objects.create(vendor=other_vendor, slug="held-slug")

    response = api_client.get(
        reverse("vendor-slug-check"), {"slug": "held-slug"}, **_auth(user)
    )
    assert response.json() == {"available": False}


@pytest.mark.django_db
def test_slug_check_own_slug_is_available(api_client, vendor_user):
    user, vendor = vendor_user
    VendorSettings.objects.create(vendor=vendor, slug="mine-slug")
    response = api_client.get(
        reverse("vendor-slug-check"), {"slug": "mine-slug"}, **_auth(user)
    )
    assert response.json() == {"available": True}


@pytest.mark.django_db
def test_slug_check_reserved(api_client, vendor_user):
    user, vendor = vendor_user
    VendorSettings.objects.create(vendor=vendor)
    response = api_client.get(
        reverse("vendor-slug-check"), {"slug": "brief"}, **_auth(user)
    )
    assert response.json() == {"available": False}


@pytest.mark.django_db
def test_slug_check_invalid_format(api_client, vendor_user):
    user, vendor = vendor_user
    VendorSettings.objects.create(vendor=vendor)
    response = api_client.get(
        reverse("vendor-slug-check"), {"slug": "Bad Slug!"}, **_auth(user)
    )
    assert response.json() == {"available": False}


@pytest.mark.django_db
def test_slug_check_normalizes_case(api_client, vendor_user):
    """A MixedCase candidate is checked as its lowercase form, so a clash with an
    existing lowercase slug is detected rather than treated as a new free slug."""
    user, vendor = vendor_user
    VendorSettings.objects.create(vendor=vendor)

    other_user = User.objects.create_user(
        email="case-holder@example.com",
        password="p@ssw0rd",
        name="Holder",
        group="VENDOR",
    )
    other_vendor = Vendor.objects.create(name="Holder Studio", owner=other_user)
    VendorSettings.objects.create(vendor=other_vendor, slug="held-slug")

    response = api_client.get(
        reverse("vendor-slug-check"), {"slug": "Held-Slug"}, **_auth(user)
    )
    assert response.json() == {"available": False}


@pytest.mark.django_db
def test_patch_normalizes_slug_case(api_client, vendor_user):
    """Saving a MixedCase slug stores the lowercase form rather than rejecting."""
    user, vendor = vendor_user
    VendorSettings.objects.create(vendor=vendor)
    response = api_client.patch(
        reverse("vendor-settings"),
        data=json.dumps({"slug": "My-Studio"}),
        content_type="application/json",
        **_auth(user),
    )
    assert response.status_code == 200
    assert response.json()["slug"] == "my-studio"
    vendor.refresh_from_db()
    assert VendorSettings.objects.get(vendor=vendor).slug == "my-studio"


@pytest.mark.django_db
def test_slug_suggest_avoids_collision(api_client, vendor_user):
    user, vendor = vendor_user
    VendorSettings.objects.create(vendor=vendor)

    other_user = User.objects.create_user(
        email="taker@example.com",
        password="p@ssw0rd",
        name="Taker",
        group="VENDOR",
    )
    other_vendor = Vendor.objects.create(name="Taker Studio", owner=other_user)
    VendorSettings.objects.create(vendor=other_vendor, slug="busy-slug")

    with patch(
        "aivus_backend.users.slug_suggest._llm_candidate", return_value="busy-slug"
    ):
        response = api_client.get(reverse("vendor-slug-suggest"), **_auth(user))

    assert response.status_code == 200
    assert response.json()["slug"] == "busy-slug-2"
