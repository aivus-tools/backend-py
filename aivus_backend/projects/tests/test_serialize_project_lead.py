"""Tests for lead markers on serialize_project (Stage 2 S2-22)."""

from __future__ import annotations

import pytest

from aivus_backend.core.enums import ProjectStatus
from aivus_backend.projects.api.serializers import serialize_project
from aivus_backend.projects.models import Brief
from aivus_backend.projects.models import Project
from aivus_backend.users.models import User
from aivus_backend.users.models import Vendor


@pytest.fixture
def vendor(db):
    user = User.objects.create_user(
        email="serialize-vendor@example.com",
        password="p@ssw0rd",
        name="Serialize Vendor",
        group="VENDOR",
    )
    return Vendor.objects.create(name="Serialize Studio", owner=user)


@pytest.mark.django_db
def test_draft_lead_in_progress_no_email(vendor):
    brief = Brief.objects.create(client=None, conversation_status="in_progress")
    project = Project.objects.create(
        vendor=vendor, brief=brief, name="Lead", status=ProjectStatus.DRAFT
    )
    data = serialize_project(project, include_relations=False)
    assert data["briefConversationStatus"] == "in_progress"
    assert data["hasContactEmail"] is False


@pytest.mark.django_db
def test_rfp_lead_with_email(vendor):
    brief = Brief.objects.create(
        client=None,
        conversation_status="finalized",
        contact_email="lead@example.com",
    )
    project = Project.objects.create(
        vendor=vendor, brief=brief, name="Lead", status=ProjectStatus.RFP
    )
    data = serialize_project(project, include_relations=False)
    assert data["briefConversationStatus"] == "finalized"
    assert data["hasContactEmail"] is True


@pytest.mark.django_db
def test_project_without_brief(vendor):
    project = Project.objects.create(
        vendor=vendor, name="No brief", status=ProjectStatus.DRAFT
    )
    data = serialize_project(project, include_relations=False)
    assert data["briefConversationStatus"] is None
    assert data["hasContactEmail"] is False
