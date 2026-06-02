"""Tests for the contact-fallback block injected into the finalize system prompt."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from aivus_backend.projects import ai_brief_v3
from aivus_backend.projects.ai_brief_v3 import _build_contact_rule
from aivus_backend.projects.models import Brief
from aivus_backend.projects.models import BriefPrompt
from aivus_backend.projects.models import ChatMessage
from aivus_backend.users.models import Client as ClientModel
from aivus_backend.users.models import User


@pytest.fixture
def owner_user(db) -> User:
    return User.objects.create_user(
        email="owner@example.com",
        password="p@ssw0rd",
        name="Owner Name",
        group="CLIENT",
    )


@pytest.fixture
def client_profile(owner_user) -> ClientModel:
    return ClientModel.objects.create(name="Acme Corp", owner=owner_user)


@pytest.fixture
def seeded_prompts(db):
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


@pytest.mark.django_db
def test_contact_rule_uses_explicit_wix_contact(client_profile):
    brief = Brief.objects.create(
        client=client_profile,
        contact_email="wix@example.com",
        contact_name="Wix Sender",
    )
    rule = _build_contact_rule(brief)
    assert "Client contact details" in rule
    assert "name: Wix Sender" in rule
    assert "email: wix@example.com" in rule
    assert "owner@example.com" not in rule
    assert "Owner Name" not in rule


@pytest.mark.django_db
def test_contact_rule_falls_back_to_owner(client_profile):
    brief = Brief.objects.create(client=client_profile)
    rule = _build_contact_rule(brief)
    assert "Client contact details" in rule
    assert "name: Owner Name" in rule
    assert "email: owner@example.com" in rule


@pytest.mark.django_db
def test_contact_rule_mixes_explicit_and_owner(client_profile):
    brief = Brief.objects.create(
        client=client_profile,
        contact_email="wix@example.com",
    )
    rule = _build_contact_rule(brief)
    assert "name: Owner Name" in rule
    assert "email: wix@example.com" in rule
    assert "owner@example.com" not in rule


@pytest.mark.django_db
def test_contact_rule_empty_when_anonymous_brief_has_no_contact(db):
    brief = Brief.objects.create(client=None, anonymous_token="tok-empty")
    rule = _build_contact_rule(brief)
    assert rule == ""


@pytest.mark.django_db
def test_contact_rule_handles_owner_with_empty_fields(db):
    user = User.objects.create_user(
        email="blank@example.com",
        password="p@ssw0rd",
        name="",
        group="CLIENT",
    )
    client = ClientModel.objects.create(name="Blank Inc", owner=user)
    brief = Brief.objects.create(client=client)
    rule = _build_contact_rule(brief)
    assert "Client contact details" in rule
    assert "email: blank@example.com" in rule
    assert "name:" not in rule


@pytest.mark.django_db
def test_generate_final_documents_injects_contact_rule(client_profile, seeded_prompts):
    """System prompt passed to call_llm_json must contain the contact block."""
    brief = Brief.objects.create(
        client=client_profile,
        conversation_status="ready_to_finalize",
        contact_email="wix@example.com",
        contact_name="",
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

    captured_messages: list = []

    def fake_call(*, model, messages, temperature, max_tokens):
        captured_messages.append(messages)
        return (
            {
                "production_brief_html": "<h1>brief</h1>",
                "vendor_email_html": "<h1>S</h1>",
                "vendor_email_text": "Subject: S",
            },
            fake_response,
        )

    with patch.object(ai_brief_v3, "call_llm_json", side_effect=fake_call):
        ai_brief_v3.generate_final_documents(brief)

    assert captured_messages, "call_llm_json was not called"
    system_message = captured_messages[0][0]
    assert system_message["role"] == "system"
    system_text = system_message["content"]
    assert "Client contact details" in system_text
    assert "email: wix@example.com" in system_text
    # falls back to owner name since contact_name is blank
    assert "name: Owner Name" in system_text


@pytest.mark.django_db
def test_generate_final_documents_skips_contact_rule_when_nothing_known(
    seeded_prompts, db
):
    """When the brief has no contact data and no client, the system prompt
    must NOT include a contact block."""
    brief = Brief.objects.create(client=None, anonymous_token="tok-noctx")
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

    captured_messages: list = []

    def fake_call(*, model, messages, temperature, max_tokens):
        captured_messages.append(messages)
        return (
            {
                "production_brief_html": "<h1>brief</h1>",
                "vendor_email_html": "<h1>S</h1>",
                "vendor_email_text": "Subject: S",
            },
            fake_response,
        )

    with patch.object(ai_brief_v3, "call_llm_json", side_effect=fake_call):
        ai_brief_v3.generate_final_documents(brief)

    system_text = captured_messages[0][0]["content"]
    assert "Client contact details" not in system_text
