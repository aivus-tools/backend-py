"""Tests for vendor-read of brief documents and PDF (Stage 2 S2-10)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.conf import settings as django_settings
from django.test import Client as DjangoTestClient
from django.urls import reverse

from aivus_backend.core.enums import ProjectStatus
from aivus_backend.projects.models import Brief
from aivus_backend.projects.models import BriefFinalDocument
from aivus_backend.projects.models import Project
from aivus_backend.users.models import User
from aivus_backend.users.models import Vendor


@pytest.fixture
def api_client() -> DjangoTestClient:
    return DjangoTestClient()


def _make_vendor(email: str, name: str):
    user = User.objects.create_user(
        email=email, password="p@ssw0rd", name=name, group="VENDOR"
    )
    vendor = Vendor.objects.create(name=name, owner=user)
    return user, vendor


def _auth(user) -> dict:
    return {
        "HTTP_X_API_KEY": django_settings.API_KEY,
        "HTTP_X_USER_ID": str(user.id),
        "HTTP_X_USER_GROUP": user.group,
    }


@pytest.fixture
def vendor_project_brief(db):
    user, vendor = _make_vendor("read-vendor@example.com", "Read Studio")
    brief = Brief.objects.create(client=None, conversation_status="finalized")
    document = BriefFinalDocument.objects.create(
        brief=brief, kind="production_brief", html="<p>brief body</p>"
    )
    project = Project.objects.create(
        vendor=vendor, brief=brief, name="Lead", status=ProjectStatus.RFP
    )
    return user, vendor, project, brief, document


@pytest.mark.django_db
def test_vendor_reads_brief_documents(api_client, vendor_project_brief):
    user, _vendor, project, brief, document = vendor_project_brief
    response = api_client.get(
        reverse("projects_api:vendor_project_brief_documents", args=[project.id]),
        **_auth(user),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["briefId"] == str(brief.id)
    assert body["documents"][0]["id"] == str(document.id)


@pytest.mark.django_db
def test_other_vendor_cannot_read_documents(api_client, vendor_project_brief):
    _user, _vendor, project, _brief, _document = vendor_project_brief
    other_user, _other_vendor = _make_vendor("other@example.com", "Other Studio")
    response = api_client.get(
        reverse("projects_api:vendor_project_brief_documents", args=[project.id]),
        **_auth(other_user),
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_vendor_downloads_brief_pdf(api_client, vendor_project_brief):
    user, _vendor, project, _brief, document = vendor_project_brief
    with patch(
        "aivus_backend.projects.brief_pdf.render_final_document_pdf",
        return_value=b"%PDF-1.4 tiny",
    ):
        response = api_client.get(
            reverse(
                "projects_api:vendor_project_brief_document_pdf",
                args=[project.id, document.id],
            ),
            **_auth(user),
        )
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"


@pytest.mark.django_db
def test_other_vendor_cannot_download_pdf(api_client, vendor_project_brief):
    _user, _vendor, project, _brief, document = vendor_project_brief
    other_user, _other_vendor = _make_vendor("other2@example.com", "Other2 Studio")
    response = api_client.get(
        reverse(
            "projects_api:vendor_project_brief_document_pdf",
            args=[project.id, document.id],
        ),
        **_auth(other_user),
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_vendor_pdf_unknown_document_404(api_client, vendor_project_brief):
    user, _vendor, project, _brief, _document = vendor_project_brief
    import uuid

    response = api_client.get(
        reverse(
            "projects_api:vendor_project_brief_document_pdf",
            args=[project.id, uuid.uuid4()],
        ),
        **_auth(user),
    )
    assert response.status_code == 404
