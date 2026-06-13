"""Tests for the client sent-to-vendor briefs lookup (Stage 2)."""

from __future__ import annotations

import pytest
from django.conf import settings as django_settings
from django.test import Client as DjangoTestClient
from django.urls import reverse

from aivus_backend.core.enums import ProjectStatus
from aivus_backend.projects.models import Brief
from aivus_backend.projects.models import Project
from aivus_backend.users.models import Client as ClientModel
from aivus_backend.users.models import User
from aivus_backend.users.models import Vendor
from aivus_backend.users.models import VendorSettings


@pytest.fixture
def api_client() -> DjangoTestClient:
    return DjangoTestClient()


@pytest.fixture
def vendor(db):
    user = User.objects.create_user(
        email="stv-vendor@example.com",
        password="p@ssw0rd",
        name="STV Vendor",
        group="VENDOR",
    )
    vendor = Vendor.objects.create(name="STV Studio", owner=user)
    VendorSettings.objects.create(vendor=vendor, slug="stv-studio")
    return vendor


@pytest.fixture
def client_user(db):
    user = User.objects.create_user(
        email="stv-client@example.com",
        password="p@ssw0rd",
        name="STV Client",
        group="CLIENT",
    )
    client_profile = ClientModel.objects.create(name="STV Client Co", owner=user)
    return user, client_profile


def _auth(user) -> dict:
    return {
        "HTTP_X_API_KEY": django_settings.API_KEY,
        "HTTP_X_USER_ID": str(user.id),
        "HTTP_X_USER_GROUP": user.group,
    }


@pytest.mark.django_db
def test_returns_briefs_sent_to_vendor(api_client, vendor, client_user):
    user, client_profile = client_user
    sent = Brief.objects.create(client=client_profile, conversation_status="finalized")
    Project.objects.create(
        vendor=vendor, brief=sent, name="lead", status=ProjectStatus.RFP
    )

    response = api_client.get(
        reverse("projects_api:client_brief_ai_sent_to_vendor"),
        {"slug": "stv-studio"},
        **_auth(user),
    )

    assert response.status_code == 200
    assert response.json() == {"briefIds": [str(sent.id)]}


@pytest.mark.django_db
def test_excludes_draft_lead_not_yet_sent(api_client, vendor, client_user):
    user, client_profile = client_user
    draft = Brief.objects.create(client=client_profile)
    Project.objects.create(
        vendor=vendor, brief=draft, name="lead", status=ProjectStatus.DRAFT
    )

    response = api_client.get(
        reverse("projects_api:client_brief_ai_sent_to_vendor"),
        {"slug": "stv-studio"},
        **_auth(user),
    )

    assert response.json() == {"briefIds": []}


@pytest.mark.django_db
def test_excludes_other_clients_brief(api_client, vendor, client_user):
    user, _client_profile = client_user
    other_user = User.objects.create_user(
        email="other-client@example.com",
        password="p@ssw0rd",
        name="Other Client",
        group="CLIENT",
    )
    other_client = ClientModel.objects.create(name="Other Co", owner=other_user)
    other_brief = Brief.objects.create(client=other_client)
    Project.objects.create(
        vendor=vendor, brief=other_brief, name="lead", status=ProjectStatus.RFP
    )

    response = api_client.get(
        reverse("projects_api:client_brief_ai_sent_to_vendor"),
        {"slug": "stv-studio"},
        **_auth(user),
    )

    assert response.json() == {"briefIds": []}


@pytest.mark.django_db
def test_excludes_other_vendor(api_client, vendor, client_user):
    user, client_profile = client_user
    other_owner = User.objects.create_user(
        email="other-vendor@example.com",
        password="p@ssw0rd",
        name="Other Vendor",
        group="VENDOR",
    )
    other_vendor = Vendor.objects.create(name="Other Studio", owner=other_owner)
    VendorSettings.objects.create(vendor=other_vendor, slug="other-studio")
    brief = Brief.objects.create(client=client_profile)
    Project.objects.create(
        vendor=other_vendor, brief=brief, name="lead", status=ProjectStatus.RFP
    )

    response = api_client.get(
        reverse("projects_api:client_brief_ai_sent_to_vendor"),
        {"slug": "stv-studio"},
        **_auth(user),
    )

    assert response.json() == {"briefIds": []}


@pytest.mark.django_db
def test_unknown_slug_returns_empty(api_client, vendor, client_user):
    user, _client_profile = client_user
    response = api_client.get(
        reverse("projects_api:client_brief_ai_sent_to_vendor"),
        {"slug": "ghost-slug"},
        **_auth(user),
    )
    assert response.json() == {"briefIds": []}


@pytest.mark.django_db
def test_missing_slug_returns_empty(api_client, vendor, client_user):
    user, _client_profile = client_user
    response = api_client.get(
        reverse("projects_api:client_brief_ai_sent_to_vendor"),
        **_auth(user),
    )
    assert response.json() == {"briefIds": []}
