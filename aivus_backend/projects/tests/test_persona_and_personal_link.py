"""Persona voice, personal-link overlay and in-chat contact capture.

Covers 869dtzup3 (Aivus speaks as the vendor), 869e0fc7f (editable personal-link
overlay layered on the base prompt) and the in-chat contact collection that
feeds the personal-link send flow (869dtzwkc / 869e0fbyq).
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from django.test import Client as DjangoTestClient
from django.urls import reverse
from django.utils import timezone

from aivus_backend.core.enums import BriefSource
from aivus_backend.projects import ai_brief_v3
from aivus_backend.projects.ai_brief_v3 import _build_persona_rule
from aivus_backend.projects.ai_brief_v3 import _build_personal_link_rule
from aivus_backend.projects.ai_brief_v3 import _resolve_brief_vendor
from aivus_backend.projects.models import Brief
from aivus_backend.projects.models import BriefPrompt
from aivus_backend.projects.models import ChatMessage
from aivus_backend.projects.models import Project
from aivus_backend.users.models import User
from aivus_backend.users.models import Vendor
from aivus_backend.users.models import VendorSettings


@pytest.fixture
def api_client() -> DjangoTestClient:
    return DjangoTestClient()


@pytest.fixture
def vendor(db) -> Vendor:
    user = User.objects.create_user(
        email="persona-vendor@example.com",
        password="p@ssw0rd",
        name="Vendor Owner",
        group="VENDOR",
    )
    return Vendor.objects.create(name="Pixel Forge", owner=user)


def _make_vendor(slug: str) -> Vendor:
    user = User.objects.create_user(
        email=f"persona-{slug}@example.com",
        password="p@ssw0rd",
        name="Owner",
        group="VENDOR",
    )
    return Vendor.objects.create(name="Bare Studio", owner=user)


@pytest.fixture
def seeded_prompts(db):
    for slug in (
        "main_system_prompt",
        "master_brief_template",
        "archetypes_reference",
        "finalization_prompt",
        "personal_link_prompt",
    ):
        BriefPrompt.objects.get_or_create(
            slug=slug,
            is_active=True,
            defaults={
                "title": slug,
                "body": f"test {slug} body",
                "version": 1,
                "model_name": "gemini-3.1-pro-preview",
            },
        )


def _brief(vendor: Vendor, source: str, *, company="", agency="") -> Brief:
    brief = Brief.objects.create(
        client=None,
        anonymous_token=f"tok-{source}",
        source=source,
        document_language="en",
    )
    if company or agency:
        VendorSettings.objects.create(
            vendor=vendor, company_name=company, agency_name=agency
        )
    Project.objects.create(vendor=vendor, brief=brief, name="Project")
    return brief


def _fake_response():
    return type(
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


def _named_vendor(name: str, email_slug: str) -> Vendor:
    user = User.objects.create_user(
        email=f"persona-{email_slug}@example.com",
        password="p@ssw0rd",
        name="Owner",
        group="VENDOR",
    )
    return Vendor.objects.create(name=name, owner=user)


def _capture_turn_system(brief: Brief) -> str:
    """Run one pre-finalize chat turn with the LLM mocked, return the system text."""
    captured: list = []

    def fake_call(*, model, messages, temperature, max_tokens):
        captured.append(messages)
        return ({"reply": "ok", "ready_to_finalize": False}, _fake_response())

    with patch.object(ai_brief_v3, "call_llm_json", side_effect=fake_call):
        ai_brief_v3.process_brief_turn(brief, "hello")
    return captured[0][0]["content"]


def _chat_result(**overrides) -> dict:
    result = {
        "reply": "ok",
        "ready_to_finalize": False,
        "conversation_status": "in_progress",
        "document_language": "en",
        "input_tokens": 10,
        "output_tokens": 5,
        "cost_usd": 0.0001,
        "model_used": "gemini-3.1-pro-preview",
        "traces": [],
        "contact_email": "",
        "contact_name": "",
    }
    result.update(overrides)
    return result


def _post_chat(api_client: DjangoTestClient, brief: Brief):
    return api_client.post(
        reverse("projects_api:public_brief_ai_chat", args=[brief.id]),
        data=json.dumps({"message": "hi"}),
        content_type="application/json",
        HTTP_X_BRIEF_TOKEN=brief.anonymous_token,
    )


# --- _build_persona_rule -----------------------------------------------------


@pytest.mark.django_db
def test_persona_uses_company_name_on_personal_link(vendor):
    brief = _brief(vendor, BriefSource.PERSONAL_LINK, company="Pixel Forge Films")
    rule = _build_persona_rule(brief)
    assert "VENDOR PERSONA" in rule
    assert "Pixel Forge Films" in rule


@pytest.mark.django_db
def test_persona_falls_back_agency_then_vendor_name(vendor):
    brief_agency = _brief(vendor, BriefSource.PERSONAL_LINK, agency="Forge Agency")
    assert "Forge Agency" in _build_persona_rule(brief_agency)

    # A different vendor with no settings row at all → fall back to vendor.name.
    bare_vendor = _make_vendor("bare")
    brief_bare = _brief(bare_vendor, BriefSource.WEBHOOK)
    assert "Bare Studio" in _build_persona_rule(brief_bare)


@pytest.mark.django_db
def test_persona_applies_to_webhook(vendor):
    brief = _brief(vendor, BriefSource.WEBHOOK, company="Forge Co")
    assert "Forge Co" in _build_persona_rule(brief)


@pytest.mark.django_db
def test_persona_empty_for_direct_brief(vendor):
    brief = Brief.objects.create(
        client=None, anonymous_token="tok-direct", source=BriefSource.DIRECT
    )
    assert _build_persona_rule(brief) == ""


# --- _build_personal_link_rule -----------------------------------------------


@pytest.mark.django_db
def test_overlay_only_for_personal_link(vendor):
    body = "Behave as the vendor's producer."
    assert "PERSONAL LINK BEHAVIOR" in _build_personal_link_rule(
        _brief(vendor, BriefSource.PERSONAL_LINK), body
    )
    assert _build_personal_link_rule(_brief(vendor, BriefSource.WEBHOOK), body) == ""
    direct = Brief.objects.create(
        client=None, anonymous_token="tok-d2", source=BriefSource.DIRECT
    )
    assert _build_personal_link_rule(direct, body) == ""


@pytest.mark.django_db
def test_overlay_empty_when_body_blank(vendor):
    brief = _brief(vendor, BriefSource.PERSONAL_LINK)
    assert _build_personal_link_rule(brief, "   ") == ""


# --- process_brief_turn injection & contact passthrough ----------------------


@pytest.mark.django_db
def test_turn_injects_persona_and_overlay_for_personal_link(vendor, seeded_prompts):
    BriefPrompt.objects.filter(slug="personal_link_prompt").update(
        body="Collect the client's email and name before finalizing."
    )
    brief = _brief(vendor, BriefSource.PERSONAL_LINK, company="Pixel Forge Films")
    captured: list = []

    def fake_call(*, model, messages, temperature, max_tokens):
        captured.append(messages)
        return ({"reply": "ok", "ready_to_finalize": False}, _fake_response())

    with patch.object(ai_brief_v3, "call_llm_json", side_effect=fake_call):
        ai_brief_v3.process_brief_turn(brief, "hello")

    system_text = captured[0][0]["content"]
    assert "VENDOR PERSONA" in system_text
    assert "Pixel Forge Films" in system_text
    assert "PERSONAL LINK BEHAVIOR" in system_text
    assert "Collect the client's email and name" in system_text


@pytest.mark.django_db
def test_turn_no_overlay_for_webhook_but_persona_stays(vendor, seeded_prompts):
    brief = _brief(vendor, BriefSource.WEBHOOK, company="Forge Co")
    captured: list = []

    def fake_call(*, model, messages, temperature, max_tokens):
        captured.append(messages)
        return ({"reply": "ok", "ready_to_finalize": False}, _fake_response())

    with patch.object(ai_brief_v3, "call_llm_json", side_effect=fake_call):
        ai_brief_v3.process_brief_turn(brief, "hello")

    system_text = captured[0][0]["content"]
    assert "VENDOR PERSONA" in system_text
    assert "PERSONAL LINK BEHAVIOR" not in system_text


@pytest.mark.django_db
def test_turn_returns_contact_from_model(vendor, seeded_prompts):
    brief = _brief(vendor, BriefSource.PERSONAL_LINK, company="Pixel Forge Films")

    def fake_call(*, model, messages, temperature, max_tokens):
        return (
            {
                "reply": "got it",
                "ready_to_finalize": False,
                "contact_email": "Jane@Example.com",
                "contact_name": "Jane Roe",
            },
            _fake_response(),
        )

    with patch.object(ai_brief_v3, "call_llm_json", side_effect=fake_call):
        result = ai_brief_v3.process_brief_turn(brief, "I'm Jane, jane@example.com")

    assert result["contact_email"] == "Jane@Example.com"
    assert result["contact_name"] == "Jane Roe"


# --- end-to-end contact persistence through the public chat endpoint ----------


@pytest.mark.django_db
def test_personal_link_chat_persists_contact(api_client, vendor, seeded_prompts):
    brief = _brief(vendor, BriefSource.PERSONAL_LINK, company="Pixel Forge Films")

    fake_result = {
        "reply": "thanks Jane",
        "ready_to_finalize": False,
        "conversation_status": "in_progress",
        "document_language": "en",
        "input_tokens": 10,
        "output_tokens": 5,
        "cost_usd": 0.0001,
        "model_used": "gemini-3.1-pro-preview",
        "traces": [],
        "contact_email": "jane@example.com",
        "contact_name": "Jane Roe",
    }
    with patch(
        "aivus_backend.projects.api.views_brief_v3.process_brief_turn",
        return_value=fake_result,
    ):
        resp = api_client.post(
            reverse("projects_api:public_brief_ai_chat", args=[brief.id]),
            data=json.dumps({"message": "I'm Jane, jane@example.com"}),
            content_type="application/json",
            HTTP_X_BRIEF_TOKEN=brief.anonymous_token,
        )

    assert resp.status_code == 200
    brief.refresh_from_db()
    assert brief.contact_email == "jane@example.com"
    assert brief.contact_name == "Jane Roe"


# --- _vendor_display_name fallback chain --------------------------------------


@pytest.mark.django_db
def test_display_name_company_wins_over_agency(vendor):
    brief = _brief(
        vendor,
        BriefSource.PERSONAL_LINK,
        company="Pixel Forge Films",
        agency="Forge Agency",
    )
    rule = _build_persona_rule(brief)
    assert "Pixel Forge Films" in rule
    assert "Forge Agency" not in rule


@pytest.mark.django_db
def test_display_name_whitespace_company_falls_to_agency(vendor):
    brief = _brief(
        vendor,
        BriefSource.PERSONAL_LINK,
        company="   ",
        agency="Forge Agency",
    )
    assert "Forge Agency" in _build_persona_rule(brief)


@pytest.mark.django_db
def test_display_name_blank_settings_row_falls_to_vendor_name(vendor):
    VendorSettings.objects.create(vendor=vendor, company_name="", agency_name="")
    brief = _brief(vendor, BriefSource.PERSONAL_LINK)
    rule = _build_persona_rule(brief)
    assert "VENDOR PERSONA" in rule
    assert "Pixel Forge" in rule


@pytest.mark.django_db
def test_persona_empty_when_all_names_blank():
    blank_vendor = _named_vendor("   ", "blank")
    VendorSettings.objects.create(vendor=blank_vendor, company_name="", agency_name="")
    brief = _brief(blank_vendor, BriefSource.PERSONAL_LINK)
    assert _build_persona_rule(brief) == ""


# --- _resolve_brief_vendor soft-delete handling ------------------------------


@pytest.mark.django_db
def test_persona_empty_when_only_project_soft_deleted(vendor):
    brief = _brief(vendor, BriefSource.PERSONAL_LINK, company="Pixel Forge Films")
    Project.objects.filter(brief=brief).update(deleted_at=timezone.now())
    assert _resolve_brief_vendor(brief) == (None, None)
    assert _build_persona_rule(brief) == ""


@pytest.mark.django_db
def test_resolve_vendor_skips_deleted_and_picks_active(vendor):
    brief = _brief(vendor, BriefSource.PERSONAL_LINK)
    deleted_project_id = Project.objects.get(brief=brief, vendor=vendor).id

    live_vendor = _named_vendor("Live Studio", "liveco")
    VendorSettings.objects.create(vendor=live_vendor, company_name="Live Co")
    Project.objects.create(vendor=live_vendor, brief=brief, name="Live Project")

    Project.objects.filter(id=deleted_project_id).update(deleted_at=timezone.now())

    resolved_vendor, _settings = _resolve_brief_vendor(brief)
    assert resolved_vendor.id == live_vendor.id
    assert "Live Co" in _build_persona_rule(brief)


# --- persona/overlay injection across the three LLM entry points --------------


@pytest.mark.django_db
def test_finalized_turn_injects_persona_without_overlay(vendor, seeded_prompts):
    BriefPrompt.objects.filter(slug="personal_link_prompt").update(
        body="OVERLAY_MARKER_ZZZ"
    )
    brief = _brief(vendor, BriefSource.PERSONAL_LINK, company="Pixel Forge Films")
    captured: list = []

    def fake_call(*, model, messages, temperature, max_tokens):
        captured.append(messages)
        return ({"reply": "ok", "edits": []}, _fake_response())

    with patch.object(ai_brief_v3, "call_llm_json", side_effect=fake_call):
        ai_brief_v3.process_finalized_turn(brief, "tweak it")

    system_text = captured[0][0]["content"]
    assert "VENDOR PERSONA" in system_text
    assert "Pixel Forge Films" in system_text
    assert "PERSONAL LINK BEHAVIOR" not in system_text
    assert "OVERLAY_MARKER_ZZZ" not in system_text


@pytest.mark.django_db
def test_generate_final_documents_omits_persona_and_overlay(vendor, seeded_prompts):
    BriefPrompt.objects.filter(slug="personal_link_prompt").update(
        body="OVERLAY_MARKER_ZZZ"
    )
    brief = _brief(vendor, BriefSource.PERSONAL_LINK, company="Pixel Forge Films")
    ChatMessage.objects.create(brief=brief, role="user", content="hi there")
    captured: list = []

    def fake_call(*, model, messages, temperature, max_tokens):
        captured.append(messages)
        return (
            {
                "production_brief_html": "<p>x</p>",
                "vendor_email_html": "<p>e</p>",
                "vendor_email_text": "e",
            },
            _fake_response(),
        )

    with patch.object(ai_brief_v3, "call_llm_json", side_effect=fake_call):
        ai_brief_v3.generate_final_documents(brief)

    system_text = captured[0][0]["content"]
    assert "VENDOR PERSONA" not in system_text
    assert "PERSONAL LINK BEHAVIOR" not in system_text
    assert "Pixel Forge Films" not in system_text
    assert "OVERLAY_MARKER_ZZZ" not in system_text


@pytest.mark.django_db
def test_system_prompt_orders_overlay_persona_before_language(vendor, seeded_prompts):
    BriefPrompt.objects.filter(slug="personal_link_prompt").update(
        body="Collect the client's email and name before finalizing."
    )
    brief = _brief(vendor, BriefSource.PERSONAL_LINK, company="Pixel Forge Films")
    text = _capture_turn_system(brief)
    assert (
        text.index("PERSONAL LINK BEHAVIOR")
        < text.index("VENDOR PERSONA")
        < text.index("=== LANGUAGE & MARKET")
    )


@pytest.mark.django_db
def test_vendor_guidance_stays_last_after_persona_and_language(vendor, seeded_prompts):
    VendorSettings.objects.create(
        vendor=vendor,
        company_name="Pixel Forge Films",
        custom_ai_instructions="Focus on budget details.",
    )
    brief = _brief(vendor, BriefSource.PERSONAL_LINK)
    text = _capture_turn_system(brief)
    assert text.index("VENDOR GUIDANCE") > text.index("VENDOR PERSONA")
    assert text.index("VENDOR GUIDANCE") > text.index("=== LANGUAGE & MARKET")
    assert text.index("Focus on budget details.") > text.index("VENDOR PERSONA")
    assert text.index("Focus on budget details.") > text.index("=== LANGUAGE & MARKET")


@pytest.mark.django_db
def test_turn_overlay_absent_when_slug_body_blank_persona_stays(vendor, seeded_prompts):
    BriefPrompt.objects.filter(slug="personal_link_prompt").update(body="   ")
    brief = _brief(vendor, BriefSource.PERSONAL_LINK, company="Pixel Forge Films")
    text = _capture_turn_system(brief)
    assert "PERSONAL LINK BEHAVIOR" not in text
    assert "VENDOR PERSONA" in text


# --- process_brief_turn contact passthrough ----------------------------------


@pytest.mark.django_db
def test_turn_strips_whitespace_from_contact_fields(vendor, seeded_prompts):
    brief = _brief(vendor, BriefSource.PERSONAL_LINK, company="Pixel Forge Films")

    def fake_call(*, model, messages, temperature, max_tokens):
        return (
            {
                "reply": "got it",
                "ready_to_finalize": False,
                "contact_email": "  Jane@Example.com  ",
                "contact_name": "  Jane Roe  ",
            },
            _fake_response(),
        )

    with patch.object(ai_brief_v3, "call_llm_json", side_effect=fake_call):
        result = ai_brief_v3.process_brief_turn(brief, "I'm Jane")

    assert result["contact_email"] == "Jane@Example.com"
    assert result["contact_name"] == "Jane Roe"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "contact_payload",
    [
        {},
        {"contact_email": None, "contact_name": None},
    ],
)
def test_turn_contact_empty_when_model_omits_or_null(
    vendor, seeded_prompts, contact_payload
):
    brief = _brief(vendor, BriefSource.PERSONAL_LINK, company="Pixel Forge Films")

    def fake_call(*, model, messages, temperature, max_tokens):
        return (
            {"reply": "ok", "ready_to_finalize": False, **contact_payload},
            _fake_response(),
        )

    with patch.object(ai_brief_v3, "call_llm_json", side_effect=fake_call):
        result = ai_brief_v3.process_brief_turn(brief, "hi")

    assert result["contact_email"] == ""
    assert result["contact_name"] == ""


# --- contact persistence gate through the public chat endpoint ----------------


@pytest.mark.django_db
@pytest.mark.parametrize("source", [BriefSource.DIRECT, BriefSource.WEBHOOK])
def test_non_personal_link_chat_does_not_persist_contacts(api_client, vendor, source):
    brief = _brief(vendor, source)
    result = _chat_result(contact_email="mallory@example.com", contact_name="Mallory")
    with patch(
        "aivus_backend.projects.api.views_brief_v3.process_brief_turn",
        return_value=result,
    ):
        resp = _post_chat(api_client, brief)

    assert resp.status_code == 200
    brief.refresh_from_db()
    assert brief.contact_email == ""
    assert brief.contact_name == ""


@pytest.mark.django_db
@pytest.mark.parametrize("omit_keys", [False, True])
def test_personal_link_chat_empty_contacts_do_not_wipe(api_client, vendor, omit_keys):
    brief = _brief(vendor, BriefSource.PERSONAL_LINK, company="Pixel Forge Films")
    Brief.objects.filter(id=brief.id).update(
        contact_email="jane@example.com", contact_name="Jane Roe"
    )
    result = _chat_result(contact_email="", contact_name="")
    if omit_keys:
        result.pop("contact_email")
        result.pop("contact_name")

    with patch(
        "aivus_backend.projects.api.views_brief_v3.process_brief_turn",
        return_value=result,
    ):
        resp = _post_chat(api_client, brief)

    assert resp.status_code == 200
    brief.refresh_from_db()
    assert brief.contact_email == "jane@example.com"
    assert brief.contact_name == "Jane Roe"


@pytest.mark.django_db
def test_personal_link_chat_name_only_update_preserves_email(api_client, vendor):
    brief = _brief(vendor, BriefSource.PERSONAL_LINK, company="Pixel Forge Films")
    Brief.objects.filter(id=brief.id).update(
        contact_email="jane@example.com", contact_name=""
    )
    result = _chat_result(contact_email="", contact_name="Jane Roe")

    with patch(
        "aivus_backend.projects.api.views_brief_v3.process_brief_turn",
        return_value=result,
    ):
        resp = _post_chat(api_client, brief)

    assert resp.status_code == 200
    brief.refresh_from_db()
    assert brief.contact_name == "Jane Roe"
    assert brief.contact_email == "jane@example.com"


@pytest.mark.django_db
def test_personal_link_chat_correcting_email_overwrites(api_client, vendor):
    brief = _brief(vendor, BriefSource.PERSONAL_LINK, company="Pixel Forge Films")

    first = _chat_result(contact_email="jane@example.com", contact_name="Jane Roe")
    with patch(
        "aivus_backend.projects.api.views_brief_v3.process_brief_turn",
        return_value=first,
    ):
        resp = _post_chat(api_client, brief)
    assert resp.status_code == 200
    brief.refresh_from_db()
    assert brief.contact_email == "jane@example.com"
    count_after_first = brief.message_count

    second = _chat_result(contact_email="jane.doe@newco.com")
    with patch(
        "aivus_backend.projects.api.views_brief_v3.process_brief_turn",
        return_value=second,
    ):
        resp = _post_chat(api_client, brief)
    assert resp.status_code == 200
    brief.refresh_from_db()
    assert brief.contact_email == "jane.doe@newco.com"
    assert brief.message_count > count_after_first


@pytest.mark.django_db
def test_personal_link_chat_normalizes_contact_before_save(api_client, vendor):
    brief = _brief(vendor, BriefSource.PERSONAL_LINK, company="Pixel Forge Films")
    result = _chat_result(
        contact_email="  Jane@EXAMPLE.COM  ", contact_name="  Jane Roe  "
    )
    with patch(
        "aivus_backend.projects.api.views_brief_v3.process_brief_turn",
        return_value=result,
    ):
        resp = _post_chat(api_client, brief)

    assert resp.status_code == 200
    brief.refresh_from_db()
    assert brief.contact_email == "jane@example.com"
    assert brief.contact_name == "Jane Roe"


@pytest.mark.django_db
def test_personal_link_chat_ignores_invalid_contact_email(api_client, vendor):
    """A hallucinated/garbled email must not be stored: it would become the Send
    fallback and break the first Send. The name still persists."""
    brief = _brief(vendor, BriefSource.PERSONAL_LINK, company="Pixel Forge Films")
    result = _chat_result(contact_email="not-an-email", contact_name="Jane Roe")
    with patch(
        "aivus_backend.projects.api.views_brief_v3.process_brief_turn",
        return_value=result,
    ):
        resp = _post_chat(api_client, brief)

    assert resp.status_code == 200
    brief.refresh_from_db()
    assert brief.contact_email == ""
    assert brief.contact_name == "Jane Roe"


@pytest.mark.django_db
def test_finalized_turn_persona_omits_first_reply_intro(vendor, seeded_prompts):
    """The persona self-introduction is a first-reply-only instruction. Post-
    finalize edit turns keep the branded voice but must NOT be told to greet and
    introduce, or the model re-introduces itself on every document tweak."""
    brief = _brief(vendor, BriefSource.PERSONAL_LINK, company="Pixel Forge Films")

    live = _capture_turn_system(brief)
    assert "introduce yourself" in live

    captured: list = []

    def fake_call(*, model, messages, temperature, max_tokens):
        captured.append(messages)
        return ({"reply": "ok", "edits": []}, _fake_response())

    with patch.object(ai_brief_v3, "call_llm_json", side_effect=fake_call):
        ai_brief_v3.process_finalized_turn(brief, "tweak it")

    finalized = captured[0][0]["content"]
    assert "VENDOR PERSONA" in finalized
    assert "Pixel Forge Films" in finalized
    assert "introduce yourself" not in finalized
    assert "very first reply" not in finalized
