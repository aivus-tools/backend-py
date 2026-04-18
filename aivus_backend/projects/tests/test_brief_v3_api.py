"""Integration tests for Brief AI v3.

Covers the happy paths for draft → start → chat → finalize → edit final docs,
as well as share endpoints, magic-bytes validation, cost-visibility flag, and
GCS multimodal part building.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from django.conf import settings
from django.test import Client as DjangoTestClient
from django.urls import reverse

from aivus_backend.projects.models import Brief
from aivus_backend.projects.models import BriefAttachment
from aivus_backend.projects.models import BriefFinalDocument
from aivus_backend.projects.models import BriefPrompt
from aivus_backend.projects.models import ChatMessage
from aivus_backend.users.models import Client as ClientModel
from aivus_backend.users.models import User

# ----------------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------------


@pytest.fixture
def api_client() -> DjangoTestClient:
    return DjangoTestClient()


@pytest.fixture
def client_user(db) -> User:
    return User.objects.create_user(
        email="brief-client@example.com",
        password="p@ssw0rd",
        name="Brief Client",
        group="CLIENT",
    )


@pytest.fixture
def staff_user(db) -> User:
    user = User.objects.create_user(
        email="brief-staff@example.com",
        password="p@ssw0rd",
        name="Brief Staff",
        group="CLIENT",
    )
    user.is_staff = True
    user.save(update_fields=["is_staff"])
    return user


@pytest.fixture
def client_profile(client_user) -> ClientModel:
    return ClientModel.objects.create(name="Acme Corp", owner=client_user)


@pytest.fixture
def staff_profile(staff_user) -> ClientModel:
    return ClientModel.objects.create(name="Staff Acme", owner=staff_user)


@pytest.fixture
def seeded_prompts(db):
    """Seed the minimal prompts so ai_brief_v3 module loads without DB errors.

    Migrations create real prompts; tests reuse them if migrations ran, or
    create minimal placeholders otherwise.
    """
    for slug in (
        "main_system_prompt",
        "master_brief_template",
        "archetypes_reference",
        "finalization_prompt",
    ):
        BriefPrompt.objects.get_or_create(
            slug=slug,
            is_active=True,
            defaults={
                "title": slug,
                "body": f"test {slug}",
                "version": 1,
                "model_name": "gemini-3.1-pro-preview",
            },
        )


def _auth_headers(user: User) -> dict:
    return {
        "HTTP_X_API_KEY": settings.API_KEY,
        "HTTP_X_USER_ID": str(user.id),
        "HTTP_X_USER_GROUP": user.group,
    }


def _public_headers(token: str) -> dict:
    return {"HTTP_X_BRIEF_TOKEN": token}


# ----------------------------------------------------------------------------
# Draft / start / chat
# ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_client_creates_draft(api_client, client_user, client_profile):
    url = reverse("projects_api:client_brief_ai_drafts")
    response = api_client.post(url, **_auth_headers(client_user))
    assert response.status_code == 201
    data = response.json()
    brief_id = data["briefId"]
    assert Brief.objects.filter(id=brief_id, client=client_profile).exists()


@pytest.mark.django_db
def test_client_start_after_draft_with_attachment(
    api_client, client_user, client_profile, seeded_prompts, monkeypatch
):
    # 1. Draft
    draft_resp = api_client.post(
        reverse("projects_api:client_brief_ai_drafts"), **_auth_headers(client_user)
    )
    brief_id = draft_resp.json()["briefId"]

    # 2. Upload attachment (bypass python-magic sniffing for simplicity)
    monkeypatch.setattr(
        "aivus_backend.projects.api.views_brief_v3._sniff_mime",
        lambda *_args, **_kw: "application/pdf",
    )
    pdf_bytes = b"%PDF-1.4\n% tiny pdf\n"
    upload_resp = api_client.post(
        reverse("projects_api:client_brief_ai_attachments", args=[brief_id]),
        data={"file": _file("brief.pdf", pdf_bytes, "application/pdf")},
        **_auth_headers(client_user),
    )
    assert upload_resp.status_code == 201
    attachment_id = upload_resp.json()["id"]

    # 3. Start with attachment — mock Celery task
    task_mock = _TaskMock(task_id="task-start-1")
    with patch(
        "aivus_backend.projects.api.views_brief_v3.generate_first_reply_task.delay",
        return_value=task_mock,
    ):
        start_resp = api_client.post(
            reverse("projects_api:client_brief_ai_start", args=[brief_id]),
            data=json.dumps(
                {
                    "message": "Нужен ролик для геймеров",
                    "attachmentIds": [attachment_id],
                }
            ),
            content_type="application/json",
            **_auth_headers(client_user),
        )
    assert start_resp.status_code == 201
    assert start_resp.json()["taskId"] == "task-start-1"

    # Attachment is linked to the user message
    first_user_msg = ChatMessage.objects.get(brief_id=brief_id, role="user")
    assert BriefAttachment.objects.filter(
        id=attachment_id, message=first_user_msg
    ).exists()


@pytest.mark.django_db
def test_client_chat_turn_writes_assistant_message(
    api_client, client_user, client_profile, seeded_prompts
):
    brief = Brief.objects.create(client=client_profile)
    ChatMessage.objects.create(brief=brief, user=client_user, role="user", content="hi")
    Brief.objects.filter(id=brief.id).update(message_count=1)

    fake_result = {
        "reply": "hello back",
        "ready_to_finalize": False,
        "conversation_status": "in_progress",
        "document_language": "en",
        "input_tokens": 10,
        "output_tokens": 5,
        "cost_usd": 0.0001,
        "model_used": "gemini-3.1-pro-preview",
        "traces": [],
    }
    with patch(
        "aivus_backend.projects.api.views_brief_v3.process_brief_turn",
        return_value=fake_result,
    ):
        resp = api_client.post(
            reverse("projects_api:client_brief_ai_chat", args=[brief.id]),
            data=json.dumps({"message": "more questions"}),
            content_type="application/json",
            **_auth_headers(client_user),
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "hello back"
    assert ChatMessage.objects.filter(brief=brief, role="assistant").count() == 1


@pytest.mark.django_db
def test_auth_message_limit_unlimited(
    api_client, client_user, client_profile, seeded_prompts
):
    """MESSAGE_LIMIT_AUTH=0 → endpoint never rejects with 429."""
    brief = Brief.objects.create(client=client_profile, message_count=9999)
    ChatMessage.objects.create(brief=brief, user=client_user, role="user", content="hi")

    fake_result = {
        "reply": "ok",
        "ready_to_finalize": False,
        "conversation_status": "in_progress",
        "document_language": "en",
        "input_tokens": 1,
        "output_tokens": 1,
        "cost_usd": 0.00001,
        "model_used": "gemini-3.1-pro-preview",
        "traces": [],
    }
    with patch(
        "aivus_backend.projects.api.views_brief_v3.process_brief_turn",
        return_value=fake_result,
    ):
        resp = api_client.post(
            reverse("projects_api:client_brief_ai_chat", args=[brief.id]),
            data=json.dumps({"message": "a"}),
            content_type="application/json",
            **_auth_headers(client_user),
        )
    assert resp.status_code == 200


# ----------------------------------------------------------------------------
# Finalize / final docs
# ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_finalize_and_update_final_document(
    api_client, client_user, client_profile, seeded_prompts
):
    brief = Brief.objects.create(
        client=client_profile, conversation_status="ready_to_finalize"
    )
    doc = BriefFinalDocument.objects.create(
        brief=brief, kind="production_brief", html="<p>Original</p>"
    )

    patch_resp = api_client.patch(
        reverse(
            "projects_api:client_brief_ai_final_document_update",
            args=[brief.id, doc.id],
        ),
        data=json.dumps({"html": "<script>alert(1)</script><p>Updated</p>"}),
        content_type="application/json",
        **_auth_headers(client_user),
    )
    assert patch_resp.status_code == 200
    doc.refresh_from_db()
    # Script tags are sanitized away.
    assert "<script>" not in doc.html
    assert "Updated" in doc.html


@pytest.mark.django_db
def test_show_cost_flag(
    api_client, client_user, client_profile, settings, seeded_prompts
):
    brief = Brief.objects.create(client=client_profile)
    settings.SHOW_BRIEF_COST_TO_ALL = False

    resp = api_client.get(
        reverse("projects_api:client_brief_ai_detail", args=[brief.id]),
        **_auth_headers(client_user),
    )
    assert resp.status_code == 200
    assert resp.json()["showCost"] is False

    settings.SHOW_BRIEF_COST_TO_ALL = True
    resp2 = api_client.get(
        reverse("projects_api:client_brief_ai_detail", args=[brief.id]),
        **_auth_headers(client_user),
    )
    assert resp2.json()["showCost"] is True


@pytest.mark.django_db
def test_staff_always_sees_cost(
    api_client, staff_user, staff_profile, settings, seeded_prompts
):
    brief = Brief.objects.create(client=staff_profile)
    settings.SHOW_BRIEF_COST_TO_ALL = False
    resp = api_client.get(
        reverse("projects_api:client_brief_ai_detail", args=[brief.id]),
        **_auth_headers(staff_user),
    )
    assert resp.status_code == 200
    assert resp.json()["showCost"] is True


# ----------------------------------------------------------------------------
# Share
# ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_share_flow(api_client, client_user, client_profile, seeded_prompts):
    brief = Brief.objects.create(
        client=client_profile, conversation_status="finalized", title="Demo brief"
    )
    BriefFinalDocument.objects.create(
        brief=brief, kind="production_brief", html="<p>Brief body</p>"
    )
    BriefFinalDocument.objects.create(
        brief=brief, kind="vendor_email", html="<p>Email body</p>"
    )
    BriefFinalDocument.objects.create(
        brief=brief, kind="deliverables_checklist", html="<ul><li>item</li></ul>"
    )

    # Create share
    create = api_client.post(
        reverse("projects_api:client_brief_ai_share", args=[brief.id]),
        **_auth_headers(client_user),
    )
    assert create.status_code == 201
    token = create.json()["token"]

    # Public GET returns 3 documents
    public = api_client.get(
        reverse("projects_api:public_brief_share_get", args=[token])
    )
    assert public.status_code == 200
    assert len(public.json()["documents"]) == 3

    # Toggle off → public GET 404
    patch_resp = api_client.patch(
        reverse("projects_api:client_brief_ai_share", args=[brief.id]),
        data=json.dumps({"isActive": False}),
        content_type="application/json",
        **_auth_headers(client_user),
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["isActive"] is False

    public_after = api_client.get(
        reverse("projects_api:public_brief_share_get", args=[token])
    )
    assert public_after.status_code == 404


@pytest.mark.django_db
def test_share_requires_finalized(
    api_client, client_user, client_profile, seeded_prompts
):
    brief = Brief.objects.create(client=client_profile)
    resp = api_client.post(
        reverse("projects_api:client_brief_ai_share", args=[brief.id]),
        **_auth_headers(client_user),
    )
    assert resp.status_code == 400


# ----------------------------------------------------------------------------
# Magic bytes validation
# ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_attachment_mime_mismatch_rejected(
    api_client, client_user, client_profile, monkeypatch
):
    brief = Brief.objects.create(client=client_profile)
    # Client claims PDF, but libmagic sniffs plain text.
    monkeypatch.setattr(
        "aivus_backend.projects.api.views_brief_v3._sniff_mime",
        lambda *_args, **_kw: "application/x-dosexec",
    )
    resp = api_client.post(
        reverse("projects_api:client_brief_ai_attachments", args=[brief.id]),
        data={"file": _file("fake.pdf", b"not a pdf", "application/pdf")},
        **_auth_headers(client_user),
    )
    assert resp.status_code == 400


# ----------------------------------------------------------------------------
# GCS part builder
# ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_attachment_to_part_uses_gs_uri_in_gcs_mode(settings, client_profile, tmp_path):
    from aivus_backend.projects.ai_brief_v3 import _attachment_to_part

    brief = Brief.objects.create(client=client_profile)
    # Create a file on-disk (the File.open fallback), but we force GCS mode so
    # _attachment_to_part must return a file_uri without reading bytes.
    attachment = BriefAttachment.objects.create(
        brief=brief,
        file="briefs/x/y.pdf",
        filename="y.pdf",
        mime_type="application/pdf",
        size_bytes=10,
    )
    settings.STORAGE_BACKEND = "gcs"
    settings.GS_BUCKET_NAME = "aivus-test"

    part = _attachment_to_part(attachment)
    assert part is not None
    assert part["type"] == "file_uri"
    assert part["file_uri"].startswith("gs://aivus-test/briefs/x/y.pdf")


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


class _TaskMock:
    def __init__(self, task_id: str) -> None:
        self.id = task_id


def _file(name: str, content: bytes, mime: str):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile(name, content, content_type=mime)
