"""Tests for the personal vendor link flow (Stage 2 S2-3, S2-4)."""

from __future__ import annotations

import pytest
from django.test import Client as DjangoTestClient
from django.urls import reverse
from django.utils import timezone

from aivus_backend.core.enums import BriefSource
from aivus_backend.core.enums import ProjectStatus
from aivus_backend.projects.models import Brief
from aivus_backend.projects.models import Project
from aivus_backend.users.models import User
from aivus_backend.users.models import Vendor
from aivus_backend.users.models import VendorSettings


@pytest.fixture
def api_client() -> DjangoTestClient:
    return DjangoTestClient()


@pytest.fixture
def vendor_with_slug(db):
    user = User.objects.create_user(
        email="branded-vendor@example.com",
        password="p@ssw0rd",
        name="Branded Vendor",
        group="VENDOR",
    )
    vendor = Vendor.objects.create(name="Acme Films", owner=user)
    VendorSettings.objects.create(
        vendor=vendor, slug="acme-films", company_name="Acme Films Co"
    )
    return vendor


# --- S2-3: by-slug resolve ---------------------------------------------------


@pytest.mark.django_db
def test_by_slug_resolves_active_vendor(api_client, vendor_with_slug):
    response = api_client.get(
        reverse("projects_api:public_brief_ai_by_slug", args=["acme-films"])
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["vendorName"] == "Acme Films Co"
    assert body["slug"] == "acme-films"
    assert "vendorLogoUrl" in body


@pytest.mark.django_db
def test_by_slug_resolves_case_insensitively(api_client, vendor_with_slug):
    """A MixedCase link must still resolve; stored slugs are always lowercase."""
    response = api_client.get(
        reverse("projects_api:public_brief_ai_by_slug", args=["Acme-Films"])
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["slug"] == "Acme-Films"


@pytest.mark.django_db
def test_by_slug_unknown_returns_404(api_client):
    response = api_client.get(
        reverse("projects_api:public_brief_ai_by_slug", args=["nope-nope"])
    )
    assert response.status_code == 404
    assert response.json()["valid"] is False


@pytest.mark.django_db
def test_by_slug_soft_deleted_vendor_returns_404(api_client, vendor_with_slug):
    Vendor.objects.filter(id=vendor_with_slug.id).update(deleted_at=timezone.now())
    response = api_client.get(
        reverse("projects_api:public_brief_ai_by_slug", args=["acme-films"])
    )
    assert response.status_code == 404


# --- S2-4: by-slug draft -----------------------------------------------------


@pytest.mark.django_db
def test_by_slug_draft_creates_brief_and_project(api_client, vendor_with_slug):
    response = api_client.post(
        reverse("projects_api:public_brief_ai_by_slug_drafts", args=["acme-films"])
    )
    assert response.status_code == 201
    body = response.json()
    assert body["briefId"]
    assert body["token"]

    brief = Brief.objects.get(id=body["briefId"])
    assert brief.source == BriefSource.PERSONAL_LINK
    assert brief.anonymous_token == body["token"]
    assert brief.client_id is None

    project = Project.objects.get(brief=brief, vendor=vendor_with_slug)
    assert project.status == ProjectStatus.DRAFT


@pytest.mark.django_db
def test_by_slug_draft_unknown_vendor_404(api_client):
    response = api_client.post(
        reverse("projects_api:public_brief_ai_by_slug_drafts", args=["ghost-slug"])
    )
    assert response.status_code == 404
    assert Brief.objects.count() == 0
    assert Project.objects.count() == 0


@pytest.mark.django_db
def test_by_slug_draft_soft_deleted_vendor_404(api_client, vendor_with_slug):
    Vendor.objects.filter(id=vendor_with_slug.id).update(deleted_at=timezone.now())
    response = api_client.post(
        reverse("projects_api:public_brief_ai_by_slug_drafts", args=["acme-films"])
    )
    assert response.status_code == 404
    assert Brief.objects.count() == 0
