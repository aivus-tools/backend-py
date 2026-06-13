"""Tests for the per-vendor webhook lead flow (Stage 2 S2-16)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from django.conf import settings as django_settings
from django.test import Client as DjangoTestClient
from django.urls import reverse

from aivus_backend.core.enums import BriefSource
from aivus_backend.core.enums import ProjectStatus
from aivus_backend.projects.models import Brief
from aivus_backend.projects.models import Project
from aivus_backend.users.models import User
from aivus_backend.users.models import Vendor
from aivus_backend.users.models import VendorWebhookKey


@pytest.fixture
def api_client() -> DjangoTestClient:
    return DjangoTestClient()


@pytest.fixture
def vendor_with_key(db):
    user = User.objects.create_user(
        email="webhook-vendor@example.com",
        password="p@ssw0rd",
        name="Webhook Vendor",
        group="VENDOR",
    )
    vendor = Vendor.objects.create(name="Hook Studio", owner=user)
    key_row = VendorWebhookKey.objects.create(vendor=vendor)
    return user, vendor, key_row


@pytest.fixture
def webhook_url() -> str:
    return reverse("projects_api:public_brief_ai_from_webhook")


def _auth(user) -> dict:
    return {
        "HTTP_X_API_KEY": django_settings.API_KEY,
        "HTTP_X_USER_ID": str(user.id),
        "HTTP_X_USER_GROUP": user.group,
    }


@pytest.mark.django_db
def test_webhook_valid_key_creates_brief_and_project(
    api_client, webhook_url, vendor_with_key
):
    _user, vendor, key_row = vendor_with_key
    with patch("aivus_backend.projects.api.views_brief_v3.transaction.on_commit"):
        response = api_client.post(
            webhook_url,
            data=json.dumps({"email": "lead@ext.com", "message": "Need a 30s spot"}),
            content_type="application/json",
            HTTP_X_AIVUS_WEBHOOK_KEY=key_row.key,
        )

    assert response.status_code == 201
    brief = Brief.objects.get(id=response.json()["briefId"])
    assert brief.source == BriefSource.WEBHOOK
    assert brief.contact_email == "lead@ext.com"

    project = Project.objects.get(brief=brief, vendor=vendor)
    assert project.status == ProjectStatus.RFP


@pytest.mark.django_db
def test_webhook_invalid_key_401(api_client, webhook_url, vendor_with_key):
    response = api_client.post(
        webhook_url,
        data=json.dumps({"message": "hi"}),
        content_type="application/json",
        HTTP_X_AIVUS_WEBHOOK_KEY="totally-wrong-key",
    )
    assert response.status_code == 401
    assert Brief.objects.count() == 0


@pytest.mark.django_db
def test_webhook_revoked_key_401(api_client, webhook_url, vendor_with_key):
    _user, _vendor, key_row = vendor_with_key
    VendorWebhookKey.objects.filter(id=key_row.id).update(is_active=False)
    response = api_client.post(
        webhook_url,
        data=json.dumps({"message": "hi"}),
        content_type="application/json",
        HTTP_X_AIVUS_WEBHOOK_KEY=key_row.key,
    )
    assert response.status_code == 401
    assert Brief.objects.count() == 0


@pytest.mark.django_db
def test_webhook_missing_message_400(api_client, webhook_url, vendor_with_key):
    _user, _vendor, key_row = vendor_with_key
    response = api_client.post(
        webhook_url,
        data=json.dumps({"email": "lead@ext.com"}),
        content_type="application/json",
        HTTP_X_AIVUS_WEBHOOK_KEY=key_row.key,
    )
    assert response.status_code == 400
    assert Brief.objects.count() == 0


# --- key management endpoints ------------------------------------------------


@pytest.mark.django_db
def test_get_webhook_key_creates_lazily(api_client, vendor_with_key):
    user, vendor, _key = vendor_with_key
    VendorWebhookKey.objects.filter(vendor=vendor).delete()
    response = api_client.get(reverse("vendor-webhook-key"), **_auth(user))
    assert response.status_code == 200
    body = response.json()
    assert body["key"]
    assert body["isActive"] is True
    assert VendorWebhookKey.objects.filter(vendor=vendor).count() == 1


@pytest.mark.django_db
def test_rotate_webhook_key_changes_value(api_client, vendor_with_key):
    user, _vendor, key_row = vendor_with_key
    old_key = key_row.key
    response = api_client.post(reverse("vendor-webhook-key-rotate"), **_auth(user))
    assert response.status_code == 200
    new_key = response.json()["key"]
    assert new_key != old_key
    key_row.refresh_from_db()
    assert key_row.key == new_key
    assert key_row.rotated_at is not None


@pytest.mark.django_db
def test_rotated_old_key_stops_working(api_client, webhook_url, vendor_with_key):
    user, _vendor, key_row = vendor_with_key
    old_key = key_row.key
    api_client.post(reverse("vendor-webhook-key-rotate"), **_auth(user))

    response = api_client.post(
        webhook_url,
        data=json.dumps({"message": "hi"}),
        content_type="application/json",
        HTTP_X_AIVUS_WEBHOOK_KEY=old_key,
    )
    assert response.status_code == 401
