"""Tests for the vendor custom AI instructions setting (869dtzuvw)."""

from __future__ import annotations

import json

import pytest
from django.conf import settings as django_settings
from django.test import Client as DjangoTestClient
from django.urls import reverse

from aivus_backend.users.models import User
from aivus_backend.users.models import Vendor
from aivus_backend.users.models import VendorSettings


@pytest.fixture
def api_client() -> DjangoTestClient:
    return DjangoTestClient()


@pytest.fixture
def vendor_user(db):
    user = User.objects.create_user(
        email="vendor-ai-settings@example.com",
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


def _patch(api_client, user, payload):
    return api_client.patch(
        reverse("vendor-settings"),
        data=json.dumps(payload),
        content_type="application/json",
        **_auth(user),
    )


@pytest.mark.django_db
def test_model_default_is_empty_string(vendor_user):
    _user, vendor = vendor_user
    settings = VendorSettings.objects.create(vendor=vendor)
    assert settings.custom_ai_instructions == ""


@pytest.mark.django_db
def test_get_returns_custom_ai_instructions(api_client, vendor_user):
    user, vendor = vendor_user
    VendorSettings.objects.create(vendor=vendor, custom_ai_instructions="Be concise.")
    response = api_client.get(reverse("vendor-settings"), **_auth(user))
    assert response.status_code == 200
    assert response.json()["customAiInstructions"] == "Be concise."


@pytest.mark.django_db
def test_get_default_custom_ai_instructions_empty(api_client, vendor_user):
    user, _vendor = vendor_user
    response = api_client.get(reverse("vendor-settings"), **_auth(user))
    assert response.status_code == 200
    assert response.json()["customAiInstructions"] == ""


@pytest.mark.django_db
def test_patch_sets_custom_ai_instructions(api_client, vendor_user):
    user, vendor = vendor_user
    VendorSettings.objects.create(vendor=vendor)
    response = _patch(api_client, user, {"customAiInstructions": "Focus on budget."})
    assert response.status_code == 200
    assert response.json()["customAiInstructions"] == "Focus on budget."
    assert (
        VendorSettings.objects.get(vendor=vendor).custom_ai_instructions
        == "Focus on budget."
    )


@pytest.mark.django_db
def test_patch_strips_surrounding_whitespace(api_client, vendor_user):
    user, vendor = vendor_user
    VendorSettings.objects.create(vendor=vendor)
    response = _patch(api_client, user, {"customAiInstructions": "  trim me  "})
    assert response.status_code == 200
    assert response.json()["customAiInstructions"] == "trim me"


@pytest.mark.django_db
def test_patch_at_limit_is_accepted(api_client, vendor_user):
    user, vendor = vendor_user
    VendorSettings.objects.create(vendor=vendor)
    text = "a" * 500
    response = _patch(api_client, user, {"customAiInstructions": text})
    assert response.status_code == 200
    assert response.json()["customAiInstructions"] == text


@pytest.mark.django_db
def test_patch_over_limit_is_rejected(api_client, vendor_user):
    user, vendor = vendor_user
    VendorSettings.objects.create(vendor=vendor, custom_ai_instructions="keep")
    response = _patch(api_client, user, {"customAiInstructions": "a" * 501})
    assert response.status_code == 400
    assert VendorSettings.objects.get(vendor=vendor).custom_ai_instructions == "keep"


@pytest.mark.django_db
def test_patch_empty_string_clears(api_client, vendor_user):
    user, vendor = vendor_user
    VendorSettings.objects.create(vendor=vendor, custom_ai_instructions="old")
    response = _patch(api_client, user, {"customAiInstructions": ""})
    assert response.status_code == 200
    assert response.json()["customAiInstructions"] == ""


@pytest.mark.django_db
def test_patch_rejects_non_string(api_client, vendor_user):
    user, vendor = vendor_user
    VendorSettings.objects.create(vendor=vendor, custom_ai_instructions="keep")
    response = _patch(api_client, user, {"customAiInstructions": 123})
    assert response.status_code == 400
    assert VendorSettings.objects.get(vendor=vendor).custom_ai_instructions == "keep"
