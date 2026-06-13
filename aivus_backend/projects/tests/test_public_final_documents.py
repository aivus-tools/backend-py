"""Tests for token-scoped anonymous final-document GET/PATCH (Stage 2 S2-7)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from django.test import Client as DjangoTestClient
from django.urls import reverse
from django.utils import timezone

from aivus_backend.core.enums import FinalDocumentKind
from aivus_backend.core.enums import ProjectStatus
from aivus_backend.projects.models import Brief
from aivus_backend.projects.models import BriefFinalDocument
from aivus_backend.projects.models import Project
from aivus_backend.users.models import User
from aivus_backend.users.models import Vendor


@pytest.fixture
def api_client() -> DjangoTestClient:
    return DjangoTestClient()


@pytest.fixture
def anon_brief_with_document(db):
    brief = Brief.objects.create(
        client=None,
        anonymous_token="final-doc-token",
        conversation_status="ready_to_finalize",
    )
    document = BriefFinalDocument.objects.create(
        brief=brief,
        kind=FinalDocumentKind.PRODUCTION_BRIEF,
        html="<p>Original</p>",
        plain_text="Original",
    )
    return brief, document


def _run_on_commit(func):
    func()


@pytest.mark.django_db
def test_get_final_documents_with_token(api_client, anon_brief_with_document):
    brief, document = anon_brief_with_document
    response = api_client.get(
        reverse("projects_api:public_brief_ai_final_documents", args=[brief.id]),
        HTTP_X_BRIEF_TOKEN="final-doc-token",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["briefId"] == str(brief.id)
    assert len(body["documents"]) == 1
    assert body["documents"][0]["id"] == str(document.id)
    assert body["generating"] is False


@pytest.mark.django_db
def test_get_final_documents_dispatches_finalize_when_ready_and_empty(api_client):
    brief = Brief.objects.create(
        client=None,
        anonymous_token="ready-no-docs",
        conversation_status="ready_to_finalize",
    )

    with (
        patch(
            "aivus_backend.projects.api.views_brief_v3.transaction.on_commit",
            side_effect=_run_on_commit,
        ),
        patch(
            "aivus_backend.projects.api.views_brief_v3.finalize_brief_task"
        ) as finalize_mock,
    ):
        response = api_client.get(
            reverse("projects_api:public_brief_ai_final_documents", args=[brief.id]),
            HTTP_X_BRIEF_TOKEN="ready-no-docs",
        )

    assert response.status_code == 200
    body = response.json()
    assert body["documents"] == []
    assert body["generating"] is True
    assert body["conversationStatus"] == "ready_to_finalize"
    finalize_mock.apply_async.assert_called_once()
    brief.refresh_from_db()
    assert brief.pending_task_id != ""


@pytest.mark.django_db
def test_get_final_documents_does_not_redispatch_when_pending(api_client):
    brief = Brief.objects.create(
        client=None,
        anonymous_token="already-pending",
        conversation_status="ready_to_finalize",
        pending_task_id="existing-task",
    )

    with patch(
        "aivus_backend.projects.api.views_brief_v3.finalize_brief_task"
    ) as finalize_mock:
        response = api_client.get(
            reverse("projects_api:public_brief_ai_final_documents", args=[brief.id]),
            HTTP_X_BRIEF_TOKEN="already-pending",
        )

    assert response.status_code == 200
    body = response.json()
    assert body["generating"] is True
    finalize_mock.apply_async.assert_not_called()
    brief.refresh_from_db()
    assert brief.pending_task_id == "existing-task"


@pytest.mark.django_db
def test_get_final_documents_not_generating_when_present(
    api_client, anon_brief_with_document
):
    brief, _document = anon_brief_with_document
    with patch(
        "aivus_backend.projects.api.views_brief_v3.finalize_brief_task"
    ) as finalize_mock:
        response = api_client.get(
            reverse("projects_api:public_brief_ai_final_documents", args=[brief.id]),
            HTTP_X_BRIEF_TOKEN="final-doc-token",
        )

    assert response.status_code == 200
    body = response.json()
    assert body["generating"] is False
    finalize_mock.apply_async.assert_not_called()


@pytest.mark.django_db
def test_get_final_documents_wrong_token_404(api_client, anon_brief_with_document):
    brief, _document = anon_brief_with_document
    response = api_client.get(
        reverse("projects_api:public_brief_ai_final_documents", args=[brief.id]),
        HTTP_X_BRIEF_TOKEN="wrong-token",
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_patch_final_document_with_token(api_client, anon_brief_with_document):
    brief, document = anon_brief_with_document
    response = api_client.patch(
        reverse(
            "projects_api:public_brief_ai_final_document_update",
            args=[brief.id, document.id],
        ),
        data=json.dumps({"html": "<p>Edited by anon</p>", "plainText": "Edited"}),
        content_type="application/json",
        HTTP_X_BRIEF_TOKEN="final-doc-token",
    )
    assert response.status_code == 200
    document.refresh_from_db()
    assert "Edited by anon" in document.html
    assert document.plain_text == "Edited"


@pytest.mark.django_db
def test_patch_final_document_wrong_token_404(api_client, anon_brief_with_document):
    brief, document = anon_brief_with_document
    response = api_client.patch(
        reverse(
            "projects_api:public_brief_ai_final_document_update",
            args=[brief.id, document.id],
        ),
        data=json.dumps({"html": "<p>nope</p>"}),
        content_type="application/json",
        HTTP_X_BRIEF_TOKEN="wrong-token",
    )
    assert response.status_code == 404
    document.refresh_from_db()
    assert "nope" not in document.html


@pytest.mark.django_db
def test_patch_final_document_requires_html(api_client, anon_brief_with_document):
    brief, document = anon_brief_with_document
    response = api_client.patch(
        reverse(
            "projects_api:public_brief_ai_final_document_update",
            args=[brief.id, document.id],
        ),
        data=json.dumps({"plainText": "no html"}),
        content_type="application/json",
        HTTP_X_BRIEF_TOKEN="final-doc-token",
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_patch_final_document_blocked_after_send(api_client, anon_brief_with_document):
    """Once the brief is sent (project at RFP) the vendor reads this very
    document, so an anonymous PATCH after Send must be rejected (MF-6)."""
    brief, document = anon_brief_with_document
    owner = User.objects.create_user(
        email="docs-vendor@example.com", password="p@ssw0rd", group="VENDOR"
    )
    vendor = Vendor.objects.create(name="Docs Studio", owner=owner)
    Project.objects.create(
        vendor=vendor, brief=brief, name="lead", status=ProjectStatus.RFP
    )

    response = api_client.patch(
        reverse(
            "projects_api:public_brief_ai_final_document_update",
            args=[brief.id, document.id],
        ),
        data=json.dumps({"html": "<p>sneaky edit</p>"}),
        content_type="application/json",
        HTTP_X_BRIEF_TOKEN="final-doc-token",
    )
    assert response.status_code == 409
    document.refresh_from_db()
    assert "sneaky edit" not in document.html


@pytest.mark.django_db
def test_patch_allowed_when_only_soft_deleted_rfp_project(
    api_client, anon_brief_with_document
):
    """SF-5: _brief_already_sent must count only active projects. A soft-deleted
    RFP project means the brief is not really sent, so the anonymous client may
    still edit the document."""
    brief, document = anon_brief_with_document
    owner = User.objects.create_user(
        email="sf5-vendor@example.com", password="p@ssw0rd", group="VENDOR"
    )
    vendor = Vendor.objects.create(name="SF5 Studio", owner=owner)
    Project.objects.create(
        vendor=vendor,
        brief=brief,
        name="lead",
        status=ProjectStatus.RFP,
        deleted_at=timezone.now(),
    )

    response = api_client.patch(
        reverse(
            "projects_api:public_brief_ai_final_document_update",
            args=[brief.id, document.id],
        ),
        data=json.dumps({"html": "<p>still editable</p>"}),
        content_type="application/json",
        HTTP_X_BRIEF_TOKEN="final-doc-token",
    )
    assert response.status_code == 200
    document.refresh_from_db()
    assert "still editable" in document.html


@pytest.mark.django_db
def test_get_final_documents_excludes_vendor_email(
    api_client, anon_brief_with_document
):
    """MF-3: the anonymous white-label GET must never expose the vendor outreach
    email — it carries the vendor's outreach strategy and contacts (PRD §5)."""
    brief, production_doc = anon_brief_with_document
    vendor_email = BriefFinalDocument.objects.create(
        brief=brief,
        kind=FinalDocumentKind.VENDOR_EMAIL,
        html="<p>Vendor outreach strategy and contacts</p>",
        plain_text="Vendor outreach strategy and contacts",
    )

    response = api_client.get(
        reverse("projects_api:public_brief_ai_final_documents", args=[brief.id]),
        HTTP_X_BRIEF_TOKEN="final-doc-token",
    )

    assert response.status_code == 200
    body = response.json()
    returned_ids = {doc["id"] for doc in body["documents"]}
    assert str(production_doc.id) in returned_ids
    assert str(vendor_email.id) not in returned_ids
    assert "outreach strategy" not in json.dumps(body)


@pytest.mark.django_db
def test_patch_vendor_email_rejected_for_anon(api_client, anon_brief_with_document):
    """MF-3: the anonymous client must not be able to read or edit the vendor
    outreach email. The out-of-scope kind looks like a missing document (404)."""
    brief, _production_doc = anon_brief_with_document
    vendor_email = BriefFinalDocument.objects.create(
        brief=brief,
        kind=FinalDocumentKind.VENDOR_EMAIL,
        html="<p>Original vendor email</p>",
        plain_text="Original vendor email",
    )

    response = api_client.patch(
        reverse(
            "projects_api:public_brief_ai_final_document_update",
            args=[brief.id, vendor_email.id],
        ),
        data=json.dumps({"html": "<p>anon tampering</p>"}),
        content_type="application/json",
        HTTP_X_BRIEF_TOKEN="final-doc-token",
    )

    assert response.status_code == 404
    vendor_email.refresh_from_db()
    assert "tampering" not in vendor_email.html
    assert "Original vendor email" in vendor_email.html
