"""Tests for lazy Client creation on brief claim (Stage 2 S2-14)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.conf import settings as django_settings
from django.test import Client as DjangoTestClient
from django.urls import reverse

from aivus_backend.projects.models import Brief
from aivus_backend.projects.models import ChatMessage
from aivus_backend.users.api.auth_views import _try_claim_pending_brief
from aivus_backend.users.models import Client as ClientModel
from aivus_backend.users.models import User


@pytest.fixture
def api_client() -> DjangoTestClient:
    return DjangoTestClient()


@pytest.mark.django_db
def test_claim_endpoint_creates_client_lazily_without_group_change(api_client):
    user = User.objects.create_user(
        email="lead-claimer@example.com",
        password="p@ssw0rd",
        name="Lead Claimer",
        group="CLIENT",
    )
    assert not ClientModel.objects.filter(owner=user).exists()

    brief = Brief.objects.create(
        client=None,
        anonymous_token="claim-lazy-token",
        contact_email="lead-claimer@example.com",
        source="personal_link",
    )

    with patch("aivus_backend.projects.api.views_brief_v3.transaction.on_commit"):
        response = api_client.post(
            reverse("projects_api:client_brief_ai_claim", args=[brief.id]),
            HTTP_X_API_KEY=django_settings.API_KEY,
            HTTP_X_USER_ID=str(user.id),
            HTTP_X_USER_GROUP=user.group,
            HTTP_X_BRIEF_TOKEN="claim-lazy-token",
        )

    assert response.status_code == 200
    client = ClientModel.objects.get(owner=user)
    brief.refresh_from_db()
    assert brief.client_id == client.id
    assert brief.contact_email == "lead-claimer@example.com"
    user.refresh_from_db()
    assert user.group == "CLIENT"


@pytest.mark.django_db
def test_try_claim_pending_brief_creates_client_without_group_change():
    user = User.objects.create_user(
        email="pending-claimer@example.com",
        password="p@ssw0rd",
        name="Pending Claimer",
        group="CONFIRMED",
    )
    brief = Brief.objects.create(
        client=None,
        anonymous_token="pending-token",
        source="personal_link",
    )
    ChatMessage.objects.create(
        brief=brief,
        user=None,
        anonymous_token="pending-token",
        role="user",
        content="hi",
    )
    user.pending_brief_id = brief.id
    user.pending_brief_token = "pending-token"
    user.save(update_fields=["pending_brief_id", "pending_brief_token"])

    claimed = _try_claim_pending_brief(user)

    assert claimed == str(brief.id)
    client = ClientModel.objects.get(owner=user)
    brief.refresh_from_db()
    assert brief.client_id == client.id
    user.refresh_from_db()
    assert user.group == "CONFIRMED"
