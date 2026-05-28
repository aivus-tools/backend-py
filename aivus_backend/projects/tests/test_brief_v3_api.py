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
        "aivus_backend.projects.api.views_brief_v3.sniff_mime",
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
def test_auth_message_limit_enforced(
    api_client, client_user, client_profile, seeded_prompts
):
    """Authenticated chat is rejected when message_count hits MESSAGE_LIMIT_AUTH."""
    from aivus_backend.projects.api.views_brief_v3 import MESSAGE_LIMIT_AUTH

    brief = Brief.objects.create(
        client=client_profile, message_count=MESSAGE_LIMIT_AUTH
    )
    ChatMessage.objects.create(brief=brief, user=client_user, role="user", content="hi")

    resp = api_client.post(
        reverse("projects_api:client_brief_ai_chat", args=[brief.id]),
        data=json.dumps({"message": "a"}),
        content_type="application/json",
        **_auth_headers(client_user),
    )
    assert resp.status_code == 429
    assert resp.json()["error"] == "Message limit reached"


@pytest.mark.django_db
def test_auth_chat_rejected_when_brief_cost_limit_reached(
    api_client, client_user, client_profile, seeded_prompts
):
    """SEC: authenticated chat must hit a cost-cap to prevent unbounded $$."""
    from decimal import Decimal

    from aivus_backend.projects.api.views_brief_v3 import MAX_BRIEF_COST_USD

    brief = Brief.objects.create(
        client=client_profile,
        message_count=1,
        total_cost_usd=MAX_BRIEF_COST_USD + Decimal("0.01"),
    )
    ChatMessage.objects.create(brief=brief, user=client_user, role="user", content="hi")

    resp = api_client.post(
        reverse("projects_api:client_brief_ai_chat", args=[brief.id]),
        data=json.dumps({"message": "more"}),
        content_type="application/json",
        **_auth_headers(client_user),
    )
    assert resp.status_code == 429
    body = resp.json()
    assert body["code"] == "cost_limit_reached"
    assert body["limitUsd"] == str(MAX_BRIEF_COST_USD)


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
        "aivus_backend.projects.api.views_brief_v3.sniff_mime",
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


# ----------------------------------------------------------------------------
# Second-batch features: deliverables / title / feedback / settings
# ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_migrations_activate_latest_prompt_versions(seeded_prompts):
    """0029 + 0030 prompt migrations should leave v3 main and v2 finalization
    as the active rows (or at least the most recent)."""
    main = BriefPrompt.objects.filter(slug="main_system_prompt", is_active=True).first()
    final = BriefPrompt.objects.filter(
        slug="finalization_prompt", is_active=True
    ).first()
    assert main is not None
    assert final is not None
    assert main.version >= 3
    assert final.version >= 2


@pytest.mark.django_db
def test_generate_final_documents_has_no_deliverables_kind(
    client_profile, seeded_prompts
):
    """generate_final_documents must only produce production_brief + vendor_email."""
    from aivus_backend.projects import ai_brief_v3

    brief = Brief.objects.create(
        client=client_profile, conversation_status="ready_to_finalize"
    )
    ChatMessage.objects.create(brief=brief, role="user", content="hi")

    fake_response = type(
        "R",
        (),
        {
            "content": "{}",
            "model_used": "fake",
            "input_tokens": 10,
            "output_tokens": 5,
            "cost_usd": 0.01,
            "latency_ms": 100,
            "request_messages": [],
            "request_params": {},
        },
    )()

    with patch.object(
        ai_brief_v3,
        "call_llm_json",
        return_value=(
            {
                "production_brief_html": "<h1>brief</h1><h2>Deliverables</h2>",
                "vendor_email_html": "<h1>Subject</h1><p>Hi</p>",
                "vendor_email_text": "Subject: Hi\n\nBody",
            },
            fake_response,
        ),
    ):
        result = ai_brief_v3.generate_final_documents(brief)

    kinds = sorted(d.kind for d in result["documents"])
    assert kinds == ["production_brief", "vendor_email"]
    assert not BriefFinalDocument.objects.filter(
        brief=brief, kind="deliverables_checklist"
    ).exists()


@pytest.mark.django_db
def test_history_filter_skips_feedback_messages(client_profile, seeded_prompts):
    """`_build_history_messages` must drop feedback_* kinds so they never land
    in the LLM context."""
    from aivus_backend.projects.ai_brief_v3 import _build_history_messages

    brief = Brief.objects.create(client=client_profile)
    m1 = ChatMessage.objects.create(brief=brief, role="user", content="hi", kind="chat")
    m2 = ChatMessage.objects.create(
        brief=brief, role="assistant", content="hello", kind="chat"
    )
    m3 = ChatMessage.objects.create(
        brief=brief,
        role="assistant",
        content="feedback q?",
        kind="feedback_request",
    )
    m4 = ChatMessage.objects.create(
        brief=brief, role="user", content="answer", kind="chat"
    )
    m5 = ChatMessage.objects.create(
        brief=brief,
        role="assistant",
        content="thx",
        kind="feedback_reply_ack",
    )

    result = _build_history_messages([m1, m2, m3, m4, m5])
    assert len(result) == 3
    contents = [c["content"][0]["text"] for c in result]
    assert contents == ["hi", "hello", "answer"]


@pytest.mark.django_db
def test_finalize_task_creates_feedback_request_and_title(
    client_user, client_profile, seeded_prompts
):
    """finalize_brief_task seeds feedback_request message + auto-title."""
    from aivus_backend.projects import tasks

    brief = Brief.objects.create(
        client=client_profile,
        conversation_status="ready_to_finalize",
        document_language="en",
    )
    ChatMessage.objects.create(
        brief=brief,
        user=client_user,
        role="user",
        content="Need a product video for laptops",
    )

    fake_documents = [
        BriefFinalDocument(brief=brief, kind="production_brief", html="<h1>Brief</h1>"),
        BriefFinalDocument(brief=brief, kind="vendor_email", html="<h1>Hi</h1>"),
    ]
    for d in fake_documents:
        d.save()
    fake_docs = {
        "documents": fake_documents,
        "input_tokens": 100,
        "output_tokens": 50,
        "cost_usd": 0.05,
        "model_used": "fake",
        "traces": [],
    }

    class _TitleResp:
        content = "Laptop Launch Product Video"
        model_used = "fake-flash"
        input_tokens = 10
        output_tokens = 5
        cost_usd = 0.0001
        latency_ms = 120
        request_messages: list = []
        request_params: dict = {}

    with (
        patch.object(tasks, "generate_final_documents", return_value=fake_docs),
        patch("aivus_backend.projects.ai_brief_v3.call_llm", return_value=_TitleResp()),
    ):
        tasks.finalize_brief_task(str(brief.id))

    brief.refresh_from_db()
    assert brief.conversation_status == "finalized"
    assert brief.title == "Laptop Launch Product Video"
    assert ChatMessage.objects.filter(
        brief=brief, kind="feedback_request", role="assistant"
    ).exists()


@pytest.mark.django_db
def test_finalize_task_tolerates_title_failure(
    client_user, client_profile, seeded_prompts
):
    """Auto-title failure must not break finalize."""
    from aivus_backend.projects import tasks

    brief = Brief.objects.create(
        client=client_profile,
        conversation_status="ready_to_finalize",
    )
    ChatMessage.objects.create(brief=brief, user=client_user, role="user", content="x")

    fake_documents = [
        BriefFinalDocument(brief=brief, kind="production_brief", html="<h1>B</h1>"),
    ]
    for d in fake_documents:
        d.save()
    fake_docs = {
        "documents": fake_documents,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "model_used": "fake",
        "traces": [],
    }

    def _boom(*a, **k):
        msg = "title service down"
        raise RuntimeError(msg)

    with (
        patch.object(tasks, "generate_final_documents", return_value=fake_docs),
        patch("aivus_backend.projects.ai_brief_v3.call_llm", side_effect=_boom),
    ):
        tasks.finalize_brief_task(str(brief.id))

    brief.refresh_from_db()
    assert brief.conversation_status == "finalized"
    # Title may stay empty — that's fine, dashboard falls back to "Untitled".
    assert ChatMessage.objects.filter(brief=brief, kind="feedback_request").exists()


@pytest.mark.django_db
def test_post_finalize_chat_records_feedback_without_llm(
    api_client, client_user, client_profile, seeded_prompts
):
    """When the last assistant message is feedback_request, user replies are
    stored as BriefFeedback and the LLM is not invoked."""
    from aivus_backend.projects.api import views_brief_v3

    brief = Brief.objects.create(
        client=client_profile,
        conversation_status="finalized",
        document_language="ru",
    )
    ChatMessage.objects.create(
        brief=brief,
        role="assistant",
        kind="feedback_request",
        content="feedback q",
    )

    with patch.object(views_brief_v3, "process_brief_turn") as process_mock:
        resp = api_client.post(
            reverse("projects_api:client_brief_ai_chat", args=[brief.id]),
            data=json.dumps({"message": "всё ок, удобно"}),
            content_type="application/json",
            **_auth_headers(client_user),
        )

    assert resp.status_code == 200
    process_mock.assert_not_called()

    from aivus_backend.projects.models import BriefFeedback

    assert BriefFeedback.objects.filter(brief=brief).count() == 1
    assert ChatMessage.objects.filter(brief=brief, kind="feedback_reply_ack").exists()


@pytest.mark.django_db
def test_patch_brief_updates_document_language(
    api_client, client_user, client_profile, seeded_prompts
):
    brief = Brief.objects.create(client=client_profile, document_language="en")

    resp = api_client.patch(
        reverse("projects_api:client_brief_ai_detail", args=[brief.id]),
        data=json.dumps({"documentLanguage": "ru"}),
        content_type="application/json",
        **_auth_headers(client_user),
    )
    assert resp.status_code == 200
    brief.refresh_from_db()
    assert brief.document_language == "ru"


@pytest.mark.django_db
def test_patch_brief_rejects_unknown_language(
    api_client, client_user, client_profile, seeded_prompts
):
    brief = Brief.objects.create(client=client_profile, document_language="en")
    resp = api_client.patch(
        reverse("projects_api:client_brief_ai_detail", args=[brief.id]),
        data=json.dumps({"documentLanguage": "fr"}),
        content_type="application/json",
        **_auth_headers(client_user),
    )
    assert resp.status_code == 400
    brief.refresh_from_db()
    assert brief.document_language == "en"


@pytest.mark.django_db
def test_regenerate_already_finalized_brief(
    api_client, client_user, client_profile, seeded_prompts
):
    """POST /finalize on an already-finalized brief replaces final documents."""
    brief = Brief.objects.create(
        client=client_profile,
        conversation_status="finalized",
        document_language="en",
    )
    BriefFinalDocument.objects.create(
        brief=brief, kind="production_brief", html="<p>old brief</p>"
    )
    BriefFinalDocument.objects.create(
        brief=brief, kind="vendor_email", html="<p>old email</p>"
    )

    resp = api_client.post(
        reverse("projects_api:client_brief_ai_finalize", args=[brief.id]),
        **_auth_headers(client_user),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "taskId" in body


@pytest.mark.django_db
def test_finalize_rejected_when_brief_cost_limit_reached(
    api_client, client_user, client_profile, seeded_prompts
):
    from decimal import Decimal

    from aivus_backend.projects.api.views_brief_v3 import MAX_BRIEF_COST_USD

    brief = Brief.objects.create(
        client=client_profile,
        conversation_status="ready_to_finalize",
        total_cost_usd=MAX_BRIEF_COST_USD + Decimal("0.01"),
    )
    resp = api_client.post(
        reverse("projects_api:client_brief_ai_finalize", args=[brief.id]),
        **_auth_headers(client_user),
    )
    assert resp.status_code == 429
    assert resp.json()["code"] == "cost_limit_reached"


@pytest.mark.django_db
def test_finalize_task_replaces_documents_on_rerun(
    client_user, client_profile, seeded_prompts
):
    """finalize_brief_task on a finalized brief replaces final documents."""
    from aivus_backend.projects import tasks

    brief = Brief.objects.create(
        client=client_profile,
        conversation_status="finalized",
        document_language="en",
    )
    BriefFinalDocument.objects.create(
        brief=brief, kind="production_brief", html="<p>old</p>"
    )
    ChatMessage.objects.create(
        brief=brief, user=client_user, role="user", content="updated context"
    )

    def fake_generate(brief):
        BriefFinalDocument.objects.filter(brief=brief).delete()
        fresh = [
            BriefFinalDocument.objects.create(
                brief=brief, kind="production_brief", html="<p>fresh</p>"
            ),
            BriefFinalDocument.objects.create(
                brief=brief, kind="vendor_email", html="<p>fresh email</p>"
            ),
        ]
        return {
            "documents": fresh,
            "input_tokens": 1,
            "output_tokens": 1,
            "cost_usd": 0.0001,
            "model_used": "fake",
            "traces": [],
        }

    with patch.object(tasks, "generate_final_documents", side_effect=fake_generate):
        tasks.finalize_brief_task(str(brief.id))

    docs = list(brief.final_documents.order_by("kind"))
    assert {d.kind for d in docs} == {"production_brief", "vendor_email"}
    production_brief = next(d for d in docs if d.kind == "production_brief")
    assert "fresh" in production_brief.html
    assert "old" not in production_brief.html


@pytest.mark.django_db
def test_finalized_status_is_sticky_after_followup_chat(
    api_client, client_user, client_profile, seeded_prompts
):
    """After finalize, follow-up chat must NOT roll conversation_status back."""
    brief = Brief.objects.create(
        client=client_profile,
        conversation_status="finalized",
        document_language="en",
    )
    ChatMessage.objects.create(brief=brief, user=client_user, role="user", content="hi")

    fake_result = {
        "reply": "thanks for the follow-up",
        "ready_to_finalize": False,
        "conversation_status": "finalized",
        "document_language": "en",
        "input_tokens": 10,
        "output_tokens": 5,
        "cost_usd": 0.0001,
        "model_used": "gemini-3.1-pro-preview",
        "traces": [],
        "updated_documents": [],
    }
    with patch(
        "aivus_backend.projects.api.views_brief_v3.process_finalized_turn",
        return_value=fake_result,
    ):
        resp = api_client.post(
            reverse("projects_api:client_brief_ai_chat", args=[brief.id]),
            data=json.dumps({"message": "one more thing"}),
            content_type="application/json",
            **_auth_headers(client_user),
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["conversationStatus"] == "finalized"
    brief.refresh_from_db()
    assert brief.conversation_status == "finalized"


@pytest.mark.django_db
def test_process_brief_turn_handles_list_response_from_llm(
    client_user, client_profile, seeded_prompts
):
    """Gemini sometimes returns [{...}] instead of {...} — must not crash."""
    from aivus_backend.core.llm import LLMResponse
    from aivus_backend.projects.ai_brief_v3 import process_brief_turn

    brief = Brief.objects.create(client=client_profile, document_language="en")
    user_msg = ChatMessage.objects.create(
        brief=brief, user=client_user, role="user", content="hello"
    )

    fake_response = LLMResponse(
        content='[{"reply": "hi from list", "ready_to_finalize": false}]',
        model_used="gemini-3.1-pro-preview",
        input_tokens=5,
        output_tokens=2,
        cost_usd=0.0001,
        latency_ms=10,
        request_messages=[],
        request_params={},
    )
    parsed_list = [{"reply": "hi from list", "ready_to_finalize": False}]
    with patch(
        "aivus_backend.projects.ai_brief_v3.call_llm_json",
        return_value=(parsed_list, fake_response),
    ):
        result = process_brief_turn(
            brief=brief, user_message="hello", attachments=[], history=[user_msg]
        )

    assert result["reply"] == "hi from list"
    assert result["ready_to_finalize"] is False


@pytest.mark.django_db
def test_process_brief_turn_injects_anonymous_auth_rule(
    client_user, client_profile, seeded_prompts
):
    """Anonymous brief gets a 'sign up' CTA, never 'Finalize button'."""
    from aivus_backend.core.llm import LLMResponse
    from aivus_backend.projects.ai_brief_v3 import process_brief_turn

    anon_brief = Brief.objects.create(
        client=None,
        anonymous_token="tok-anon-1",
        document_language="en",
    )
    user_msg = ChatMessage.objects.create(
        brief=anon_brief,
        user=None,
        anonymous_token="tok-anon-1",
        role="user",
        content="hi",
    )

    captured = {}

    def fake_call(model, messages, **kwargs):
        captured["system"] = next(
            m["content"] for m in messages if m["role"] == "system"
        )
        return (
            {"reply": "ok", "ready_to_finalize": False},
            LLMResponse(
                content="{}",
                model_used=model,
                input_tokens=1,
                output_tokens=1,
                cost_usd=0.0,
                latency_ms=1,
                request_messages=[],
                request_params={},
            ),
        )

    with patch(
        "aivus_backend.projects.ai_brief_v3.call_llm_json", side_effect=fake_call
    ):
        process_brief_turn(
            brief=anon_brief, user_message="hi", attachments=[], history=[user_msg]
        )

    assert "USER AUTH CONTEXT" in captured["system"]
    assert "anonymously" in captured["system"]
    assert "sign up" in captured["system"].lower()
    assert "Finalize" not in captured["system"] or "never mention" in captured["system"]


@pytest.mark.django_db
def test_process_brief_turn_injects_authenticated_auth_rule(
    client_user, client_profile, seeded_prompts
):
    """Signed-in brief gets the 'Finalize button' CTA."""
    from aivus_backend.core.llm import LLMResponse
    from aivus_backend.projects.ai_brief_v3 import process_brief_turn

    brief = Brief.objects.create(client=client_profile, document_language="en")
    user_msg = ChatMessage.objects.create(
        brief=brief, user=client_user, role="user", content="hi"
    )

    captured = {}

    def fake_call(model, messages, **kwargs):
        captured["system"] = next(
            m["content"] for m in messages if m["role"] == "system"
        )
        return (
            {"reply": "ok", "ready_to_finalize": False},
            LLMResponse(
                content="{}",
                model_used=model,
                input_tokens=1,
                output_tokens=1,
                cost_usd=0.0,
                latency_ms=1,
                request_messages=[],
                request_params={},
            ),
        )

    with patch(
        "aivus_backend.projects.ai_brief_v3.call_llm_json", side_effect=fake_call
    ):
        process_brief_turn(
            brief=brief, user_message="hi", attachments=[], history=[user_msg]
        )

    assert "USER AUTH CONTEXT" in captured["system"]
    assert "signed in" in captured["system"]
    assert "Finalize" in captured["system"]
    assert (
        "sign up" not in captured["system"].lower()
        or "Never tell" in captured["system"]
    )


@pytest.mark.django_db
def test_process_brief_turn_injects_post_finalize_auth_rule(
    client_user, client_profile, seeded_prompts
):
    """Finalized brief: AI applies edits via tools and never suggests UI buttons."""
    from aivus_backend.core.llm import LLMResponse
    from aivus_backend.projects.ai_brief_v3 import process_brief_turn

    brief = Brief.objects.create(
        client=client_profile,
        document_language="en",
        conversation_status="finalized",
    )
    user_msg = ChatMessage.objects.create(
        brief=brief, user=client_user, role="user", content="rename to Acme"
    )

    captured = {}

    def fake_call(model, messages, **kwargs):
        captured["system"] = next(
            m["content"] for m in messages if m["role"] == "system"
        )
        return (
            {"reply": "ok", "ready_to_finalize": False},
            LLMResponse(
                content="{}",
                model_used=model,
                input_tokens=1,
                output_tokens=1,
                cost_usd=0.0,
                latency_ms=1,
                request_messages=[],
                request_params={},
            ),
        )

    with patch(
        "aivus_backend.projects.ai_brief_v3.call_llm_json", side_effect=fake_call
    ):
        process_brief_turn(
            brief=brief,
            user_message="rename to Acme",
            attachments=[],
            history=[user_msg],
        )

    system_prompt = captured["system"]
    assert "ALREADY been finalized" in system_prompt
    # Agent must apply edits automatically via its own tools and never tell
    # the user to click any UI button (including Regenerate/Finalize).
    assert "targeted edits" in system_prompt
    assert "Never tell the\nuser to click" in system_prompt
    assert "do not name the button" in system_prompt


@pytest.mark.django_db
def test_client_start_stores_document_language_from_body(
    api_client, client_user, client_profile, seeded_prompts
):
    """POST /start with documentLanguage persists it on the brief before task."""
    brief = Brief.objects.create(client=client_profile)

    task_mock = _TaskMock(task_id="task-lang-1")
    with patch(
        "aivus_backend.projects.api.views_brief_v3.generate_first_reply_task.delay",
        return_value=task_mock,
    ):
        resp = api_client.post(
            reverse("projects_api:client_brief_ai_start", args=[brief.id]),
            data=json.dumps({"message": "Hi", "documentLanguage": "ru"}),
            content_type="application/json",
            **_auth_headers(client_user),
        )
    assert resp.status_code == 201
    brief.refresh_from_db()
    assert brief.document_language == "ru"


@pytest.mark.django_db
def test_client_start_rejects_invalid_document_language(
    api_client, client_user, client_profile, seeded_prompts
):
    brief = Brief.objects.create(client=client_profile)
    resp = api_client.post(
        reverse("projects_api:client_brief_ai_start", args=[brief.id]),
        data=json.dumps({"message": "Hi", "documentLanguage": "fr"}),
        content_type="application/json",
        **_auth_headers(client_user),
    )
    assert resp.status_code == 400


@pytest.mark.django_db
def test_client_finalize_overrides_document_language(
    api_client, client_user, client_profile, seeded_prompts
):
    """POST /finalize with documentLanguage overrides brief.document_language."""
    brief = Brief.objects.create(
        client=client_profile,
        document_language="ru",
        conversation_status="ready_to_finalize",
    )

    task_mock = _TaskMock(task_id="task-lang-2")
    with patch(
        "aivus_backend.projects.api.views_brief_v3.finalize_brief_task.delay",
        return_value=task_mock,
    ):
        resp = api_client.post(
            reverse("projects_api:client_brief_ai_finalize", args=[brief.id]),
            data=json.dumps({"documentLanguage": "en"}),
            content_type="application/json",
            **_auth_headers(client_user),
        )
    assert resp.status_code == 200
    brief.refresh_from_db()
    assert brief.document_language == "en"


@pytest.mark.django_db
def test_client_finalize_without_body_keeps_document_language(
    api_client, client_user, client_profile, seeded_prompts
):
    brief = Brief.objects.create(
        client=client_profile,
        document_language="ru",
        conversation_status="ready_to_finalize",
    )

    task_mock = _TaskMock(task_id="task-lang-3")
    with patch(
        "aivus_backend.projects.api.views_brief_v3.finalize_brief_task.delay",
        return_value=task_mock,
    ):
        resp = api_client.post(
            reverse("projects_api:client_brief_ai_finalize", args=[brief.id]),
            **_auth_headers(client_user),
        )
    assert resp.status_code == 200
    brief.refresh_from_db()
    assert brief.document_language == "ru"


@pytest.mark.django_db
def test_generate_final_documents_uses_frozen_brief_language(
    client_user, client_profile, seeded_prompts
):
    """generate_final_documents must strictly respect brief.document_language
    even when the chat is in a different language."""
    from aivus_backend.core.llm import LLMResponse
    from aivus_backend.projects.ai_brief_v3 import generate_final_documents

    brief = Brief.objects.create(
        client=client_profile,
        document_language="en",
    )
    ChatMessage.objects.create(
        brief=brief,
        user=client_user,
        role="user",
        content="Сделай бриф про фитнес-приложение, бюджет 50к рублей.",
    )

    captured = {}

    def fake_call(model, messages, **kwargs):
        captured["system"] = next(
            m["content"] for m in messages if m["role"] == "system"
        )
        parsed = {
            "production_brief_html": "<h1>Brief</h1>",
            "vendor_email_html": "<p>hi</p>",
            "vendor_email_text": "hi",
        }
        return (
            parsed,
            LLMResponse(
                content="{}",
                model_used=model,
                input_tokens=1,
                output_tokens=1,
                cost_usd=0.0,
                latency_ms=1,
                request_messages=[],
                request_params={},
            ),
        )

    with patch(
        "aivus_backend.projects.ai_brief_v3.call_llm_json", side_effect=fake_call
    ):
        generate_final_documents(brief=brief)

    assert "Brief document language: English" in captured["system"]
    assert "Russian" not in captured["system"].split("Market")[0]


@pytest.mark.django_db
def test_public_start_stores_document_language_from_body(
    api_client, client_profile, seeded_prompts
):
    """POST /public/start also accepts documentLanguage, mirroring auth flow."""
    brief = Brief.objects.create(
        client=None,
        anonymous_token="tok-lang",
    )
    task_mock = _TaskMock(task_id="task-lang-4")
    with patch(
        "aivus_backend.projects.api.views_brief_v3.generate_first_reply_task.delay",
        return_value=task_mock,
    ):
        resp = api_client.post(
            reverse("projects_api:public_brief_ai_start", args=[brief.id]),
            data=json.dumps({"message": "Hi", "documentLanguage": "ru"}),
            content_type="application/json",
            HTTP_X_BRIEF_TOKEN="tok-lang",
        )
    assert resp.status_code == 201
    brief.refresh_from_db()
    assert brief.document_language == "ru"
