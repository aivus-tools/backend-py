"""Tests for token-scoped anonymous final-document GET/PATCH (Stage 2 S2-7)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from django.test import Client as DjangoTestClient
from django.urls import reverse

from aivus_backend.core.enums import FinalDocumentKind
from aivus_backend.projects.models import Brief
from aivus_backend.projects.models import BriefFinalDocument


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
