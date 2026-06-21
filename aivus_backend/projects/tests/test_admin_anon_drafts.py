"""Tests for the admin anonymous-draft cleanup action (Stage 2 S2-20)."""

from __future__ import annotations

from http import HTTPStatus

import pytest
from django.contrib.admin.sites import AdminSite
from django.urls import reverse

from aivus_backend.projects.admin import BriefAdmin
from aivus_backend.projects.models import Brief
from aivus_backend.users.models import Client as ClientModel
from aivus_backend.users.models import User


class _FakeRequest:
    def __init__(self):
        self.messages = []


@pytest.fixture
def brief_admin():
    return BriefAdmin(Brief, AdminSite())


@pytest.mark.django_db
def test_delete_anonymous_drafts_only_removes_unclaimed(brief_admin, monkeypatch):
    owner = User.objects.create_user(
        email="admin-owner@example.com", password="p@ssw0rd", name="Owner"
    )
    client_profile = ClientModel.objects.create(name="Owned", owner=owner)

    anon_draft = Brief.objects.create(client=None, conversation_status="in_progress")
    finalized_anon = Brief.objects.create(client=None, conversation_status="finalized")
    claimed = Brief.objects.create(
        client=client_profile, conversation_status="in_progress"
    )

    messages = []
    monkeypatch.setattr(
        brief_admin, "message_user", lambda request, msg: messages.append(msg)
    )

    brief_admin.delete_anonymous_drafts(_FakeRequest(), Brief.objects.all())

    assert not Brief.objects.filter(id=anon_draft.id).exists()
    assert Brief.objects.filter(id=finalized_anon.id).exists()
    assert Brief.objects.filter(id=claimed.id).exists()
    assert "Deleted 1" in messages[0]


@pytest.mark.django_db
def test_changelist_with_anonymous_draft_filter(admin_client):
    Brief.objects.create(client=None, conversation_status="in_progress")
    url = reverse("admin:projects_brief_changelist")
    response = admin_client.get(url, data={"anonymous_draft": "yes"})
    assert response.status_code == HTTPStatus.OK
