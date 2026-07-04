"""Tests for the personal vendor link flow (Stage 2 S2-3, S2-4)."""

from __future__ import annotations

import pytest
from django.conf import settings
from django.test import Client as DjangoTestClient
from django.urls import reverse
from django.utils import timezone

from aivus_backend.core.enums import BriefSource
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


# --- SF-2: per-vendor draft rate limit ---------------------------------------


@pytest.mark.django_db
def test_slug_drafts_vendor_rate_limit_trips_and_is_per_vendor(vendor_with_slug):
    """SF-2: the per-vendor 100/h cap bounds personal-link draft creation per
    vendor.id, so an IP-rotating botnet cannot flood one vendor's dashboard. It is
    keyed by vendor_id, so a different vendor is unaffected.

    Rate limiting is disabled in the default test settings, so we enable it and
    drive the helper directly against a cleared cache.
    """
    from django.core.cache import cache
    from django.test import RequestFactory
    from django.test import override_settings

    from aivus_backend.projects.api.views_brief_v3 import (
        _slug_drafts_vendor_ratelimited,
    )

    other_user = User.objects.create_user(
        email="other-slug-vendor@example.com",
        password="p@ssw0rd",
        name="Other Vendor",
        group="VENDOR",
    )
    other_vendor = Vendor.objects.create(name="Other Studio", owner=other_user)

    cache.clear()
    factory = RequestFactory()

    def _request():
        return factory.post("/service/public/briefs/ai/by-slug/acme-films/drafts")

    with override_settings(RATELIMIT_ENABLE=True):
        results = [
            _slug_drafts_vendor_ratelimited(_request(), vendor_with_slug)
            for _ in range(100)
        ]
        assert not any(results)
        assert _slug_drafts_vendor_ratelimited(_request(), vendor_with_slug) is True
        assert _slug_drafts_vendor_ratelimited(_request(), other_vendor) is False


@pytest.mark.django_db
def test_slug_drafts_returns_429_when_vendor_rate_limited(api_client, vendor_with_slug):
    """SF-2: when the per-vendor cap trips, the endpoint answers 429 and creates
    no brief or project."""
    from unittest.mock import patch

    with patch(
        "aivus_backend.projects.api.views_brief_v3._slug_drafts_vendor_ratelimited",
        return_value=True,
    ):
        response = api_client.post(
            reverse("projects_api:public_brief_ai_by_slug_drafts", args=["acme-films"])
        )
    assert response.status_code == 429
    assert Brief.objects.count() == 0
    assert Project.objects.count() == 0


@pytest.mark.django_db
def test_slug_drafts_vendor_rate_limit_disabled_in_tests_by_default(vendor_with_slug):
    """Without RATELIMIT_ENABLE the helper is a no-op so unrelated tests are not
    throttled by accumulated state."""
    from django.test import RequestFactory

    from aivus_backend.projects.api.views_brief_v3 import (
        _slug_drafts_vendor_ratelimited,
    )

    request = RequestFactory().post(
        "/service/public/briefs/ai/by-slug/acme-films/drafts"
    )
    assert _slug_drafts_vendor_ratelimited(request, vendor_with_slug) is False


# --- client (authenticated) by-slug draft ------------------------------------


@pytest.fixture
def client_user(db) -> User:
    return User.objects.create_user(
        email="branded-client@example.com",
        password="p@ssw0rd",
        name="Branded Client",
        group="CLIENT",
    )


@pytest.fixture
def client_profile(client_user) -> ClientModel:
    return ClientModel.objects.create(name="Client Corp", owner=client_user)


def _auth_headers(user) -> dict:
    return {
        "HTTP_X_API_KEY": settings.API_KEY,
        "HTTP_X_USER_ID": str(user.id),
        "HTTP_X_USER_GROUP": user.group,
    }


@pytest.mark.django_db
def test_client_by_slug_draft_creates_brief_and_project(
    api_client, vendor_with_slug, client_user, client_profile
):
    response = api_client.post(
        reverse("projects_api:client_brief_ai_by_slug_drafts", args=["acme-films"]),
        **_auth_headers(client_user),
    )
    assert response.status_code == 201
    brief = Brief.objects.get(id=response.json()["briefId"])
    assert brief.source == BriefSource.PERSONAL_LINK
    assert brief.client_id == client_profile.id
    assert brief.anonymous_token is None

    project = Project.objects.get(brief=brief, vendor=vendor_with_slug)
    assert project.status == ProjectStatus.DRAFT
    assert project.client_id == client_profile.id


@pytest.mark.django_db
def test_client_by_slug_draft_requires_client_profile(
    api_client, vendor_with_slug, client_user
):
    """A CLIENT user without a Client profile gets 403, no brief created."""
    response = api_client.post(
        reverse("projects_api:client_brief_ai_by_slug_drafts", args=["acme-films"]),
        **_auth_headers(client_user),
    )
    assert response.status_code == 403
    assert Brief.objects.count() == 0


@pytest.mark.django_db
def test_client_by_slug_draft_unknown_vendor_404(
    api_client, client_user, client_profile
):
    response = api_client.post(
        reverse("projects_api:client_brief_ai_by_slug_drafts", args=["ghost-slug"]),
        **_auth_headers(client_user),
    )
    assert response.status_code == 404
    assert Brief.objects.count() == 0


@pytest.mark.django_db
def test_client_by_slug_draft_requires_auth(api_client, vendor_with_slug):
    """Without CLIENT auth the endpoint is rejected and creates nothing."""
    response = api_client.post(
        reverse("projects_api:client_brief_ai_by_slug_drafts", args=["acme-films"])
    )
    assert response.status_code in (401, 403)
    assert Brief.objects.count() == 0


@pytest.mark.django_db
def test_client_and_anon_slug_drafts_use_separate_rate_buckets(vendor_with_slug):
    """The authenticated by-slug cap must not share a bucket with the anonymous
    one, so an anonymous flood on a vendor's link cannot 429 its logged-in
    clients (and vice versa)."""
    from django.core.cache import cache
    from django.test import RequestFactory
    from django.test import override_settings

    from aivus_backend.projects.api.views_brief_v3 import (
        _client_slug_drafts_vendor_ratelimited,
    )
    from aivus_backend.projects.api.views_brief_v3 import (
        _slug_drafts_vendor_ratelimited,
    )

    cache.clear()
    factory = RequestFactory()

    with override_settings(RATELIMIT_ENABLE=True):
        for _ in range(100):
            _slug_drafts_vendor_ratelimited(factory.post("/x"), vendor_with_slug)
        anon_limited = _slug_drafts_vendor_ratelimited(
            factory.post("/x"), vendor_with_slug
        )
        client_limited = _client_slug_drafts_vendor_ratelimited(
            factory.post("/x"), vendor_with_slug
        )
    # Anonymous bucket exhausted; the client bucket is independent and still open.
    assert anon_limited is True
    assert client_limited is False
