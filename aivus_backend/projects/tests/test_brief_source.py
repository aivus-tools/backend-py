"""Tests for the Brief.source attribution field (Stage 2 S2-1)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from django.test import Client as DjangoTestClient
from django.urls import reverse

from aivus_backend.core.enums import BriefSource
from aivus_backend.projects.api.serializers import serialize_brief_v3
from aivus_backend.projects.models import Brief

WEBHOOK_SECRET = "wix-test-secret"


@pytest.fixture
def api_client() -> DjangoTestClient:
    return DjangoTestClient()


@pytest.fixture
def wix_url() -> str:
    return reverse("projects_api:public_brief_ai_from_wix")


@pytest.fixture
def enable_webhook(settings):
    settings.WIX_WEBHOOK_SECRET = WEBHOOK_SECRET
    settings.FRONTEND_URL = "https://go.aivus.co"
    return settings


@pytest.mark.django_db
def test_brief_defaults_to_direct_source():
    brief = Brief.objects.create(client=None)
    assert brief.source == BriefSource.DIRECT


@pytest.mark.django_db
def test_serialize_brief_v3_exposes_source():
    brief = Brief.objects.create(client=None, source=BriefSource.PERSONAL_LINK)
    assert serialize_brief_v3(brief)["source"] == "personal_link"


@pytest.mark.django_db
def test_wix_webhook_writes_wix_source(api_client, wix_url, enable_webhook):
    with patch("aivus_backend.projects.api.views_brief_v3.transaction.on_commit"):
        response = api_client.post(
            wix_url,
            data=json.dumps({"email": "lead@example.com", "message": "30s film"}),
            content_type="application/json",
            HTTP_X_AIVUS_WEBHOOK_SECRET=WEBHOOK_SECRET,
        )

    assert response.status_code == 201
    brief = Brief.objects.get(id=response.json()["briefId"])
    assert brief.source == BriefSource.WIX
