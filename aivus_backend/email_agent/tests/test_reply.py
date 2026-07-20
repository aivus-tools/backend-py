"""Tests for the first-response engine (S3-27/28/29)."""

from unittest.mock import patch

import pytest

from aivus_backend.email_agent import classification
from aivus_backend.email_agent import reply
from aivus_backend.email_agent.models import AgentLog
from aivus_backend.email_agent.models import EmailAccount
from aivus_backend.email_agent.models import EmailAccountRole
from aivus_backend.email_agent.models import EmailDirection
from aivus_backend.email_agent.models import EmailMessage
from aivus_backend.email_agent.models import EmailThread
from aivus_backend.email_agent.models import OutboundDraft
from aivus_backend.email_agent.models import OutboundDraftStatus
from aivus_backend.projects.models import Brief
from aivus_backend.projects.models import Project
from aivus_backend.projects.models import ProjectStatus
from aivus_backend.users.models import VendorSettings

pytestmark = pytest.mark.django_db


class _FakeResponse:
    model_used = "gemini-3.1-pro-preview"
    input_tokens = 80
    output_tokens = 20
    cost_usd = 0.001
    latency_ms = 150


_SLOT_BODY = {
    "greeting": "Hi Jane,",
    "main": "Thanks for reaching out about your project.",
    "next_step": "We will get back to you shortly.",
    "signoff": "Best, the team",
}
_SLOTS = {"slots": _SLOT_BODY, "language": "en"}


@pytest.fixture
def account(vendor):
    return EmailAccount.objects.create(
        vendor=vendor,
        role=EmailAccountRole.MONITOR,
        email="monitor@vendor.com",
    )


def _thread(vendor, **over):
    defaults = {
        "vendor": vendor,
        "provider_thread_id": "t1",
        "client_email": "jane@client.com",
        "client_name": "Jane",
        "canonical_subject": "New project",
        "participants": ["jane@client.com"],
    }
    defaults.update(over)
    return EmailThread.objects.create(**defaults)


def _inbound(account, thread):
    return EmailMessage.objects.create(
        account=account,
        thread=thread,
        provider_message_id="<m1@client>",
        direction=EmailDirection.IN,
        from_email="jane@client.com",
        subject="New project",
        body_clean="We need a corporate video.",
        headers={"from": "jane@client.com"},
    )


def _classification(**over):
    base = {
        "reasoning": "",
        "intent": "order",
        "extracted": {"wants": "video", "deadline": "", "budget": "", "missing": ""},
        "action_items": [],
        "whos_ball": "agent",
        "safe_to_send": True,
        "escalate_reason": "",
        "pause_until": "",
        "language": "en",
        "urgent": False,
        "confidence": 0.9,
    }
    base.update(over)
    return classification.coerce_classification(base)


def _link_project(vendor, thread, *, slug, thread_token):
    brief = Brief.objects.create(anonymous_token=thread_token)
    project = Project.objects.create(
        vendor=vendor, brief=brief, name="Lead", status=ProjectStatus.RFP
    )
    thread.project = project
    thread.save(update_fields=["project"])
    VendorSettings.objects.create(vendor=vendor, slug=slug)
    return project


def test_decide_variant_a_when_no_link():
    result = _classification(extracted={"wants": "", "missing": "budget"})
    assert reply.decide_variant(result, has_brief_link=False) == reply.VARIANT_A


def test_decide_variant_b_when_order_and_link():
    result = _classification(intent="order", extracted={"wants": "brand video"})
    assert reply.decide_variant(result, has_brief_link=True) == reply.VARIANT_B


def test_decide_variant_a_when_follow_up_even_with_link():
    # A follow-up is a chase or a defer, never a fresh commitment — pushing the
    # brief link on it reads as tone-deaf, degrade to an acknowledgement.
    result = _classification(intent="follow_up", extracted={"wants": "brief"})
    assert reply.decide_variant(result, has_brief_link=True) == reply.VARIANT_A


def test_decide_variant_c_when_urgent():
    result = _classification(urgent=True)
    assert reply.decide_variant(result, has_brief_link=True) == reply.VARIANT_C


def test_decide_variant_c_on_budget_and_deadline():
    result = _classification(extracted={"budget": "$5k", "deadline": "Friday"})
    assert reply.decide_variant(result, has_brief_link=False) == reply.VARIANT_C


def test_has_forbidden_commitments():
    assert reply.has_forbidden_commitments("It will cost $5000.")
    assert reply.has_forbidden_commitments("We deliver in 3 days.")
    assert not reply.has_forbidden_commitments("We will be in touch soon.")


def test_build_brief_link(vendor):
    thread = _thread(vendor)
    _link_project(vendor, thread, slug="acme", thread_token="tok123")

    link = reply.build_brief_link(thread)

    assert "/brief/acme?" in link
    assert "t=tok123" in link


def test_build_brief_link_empty_without_slug(vendor):
    thread = _thread(vendor)
    brief = Brief.objects.create(anonymous_token="tok")
    project = Project.objects.create(vendor=vendor, brief=brief, name="Lead")
    thread.project = project
    thread.save(update_fields=["project"])

    assert reply.build_brief_link(thread) == ""


def test_propose_reply_fills_skeleton(account, vendor):
    thread = _thread(vendor)
    message = _inbound(account, thread)
    result = _classification()

    with patch.object(reply, "call_llm_json", return_value=(_SLOTS, _FakeResponse())):
        proposal, _trace = reply.propose_reply(message, result)

    assert proposal is not None
    assert "Hi Jane," in proposal.body
    assert "Thanks for reaching out" in proposal.body
    assert proposal.variant == reply.VARIANT_A


def test_propose_reply_blocks_forbidden_commitment(account, vendor):
    thread = _thread(vendor)
    message = _inbound(account, thread)
    result = _classification()
    payload = {"slots": {**_SLOT_BODY, "main": "It will cost $5000."}}

    with patch.object(reply, "call_llm_json", return_value=(payload, _FakeResponse())):
        proposal, _trace = reply.propose_reply(message, result)

    assert proposal is None


def test_propose_reply_strips_foreign_url(account, vendor):
    thread = _thread(vendor)
    message = _inbound(account, thread)
    result = _classification()
    payload = {"slots": {**_SLOT_BODY, "main": "See http://evil.example.com now."}}

    with patch.object(reply, "call_llm_json", return_value=(payload, _FakeResponse())):
        proposal, _trace = reply.propose_reply(message, result)

    assert proposal is not None
    assert "evil.example.com" not in proposal.body
    assert "[link removed]" in proposal.body


def test_variant_b_includes_brief_link(account, vendor):
    thread = _thread(vendor)
    _link_project(vendor, thread, slug="acme", thread_token="tok123")
    message = _inbound(account, thread)
    result = _classification(extracted={"wants": "", "missing": "budget"})

    with patch.object(reply, "call_llm_json", return_value=(_SLOTS, _FakeResponse())):
        proposal, _trace = reply.propose_reply(message, result)

    assert proposal is not None
    assert proposal.variant == reply.VARIANT_B
    assert "/brief/acme?" in proposal.body


def test_create_draft_is_idempotent(account, vendor):
    thread = _thread(vendor)
    message = _inbound(account, thread)
    proposal = reply.ReplyProposal(
        variant="A",
        action="acknowledge_receipt",
        body="Hi",
        language="en",
        confidence=0.9,
    )

    first = reply.create_draft(message, proposal)
    second = reply.create_draft(message, proposal)

    assert first is not None
    assert second is None
    assert OutboundDraft.objects.filter(in_reply_to_message=message).count() == 1


def test_handle_reply_creates_draft_and_notifies(account, vendor):
    thread = _thread(vendor)
    message = _inbound(account, thread)
    result = _classification()

    with (
        patch.object(reply, "call_llm_json", return_value=(_SLOTS, _FakeResponse())),
        patch.object(reply.notifications, "notify") as notify,
    ):
        draft = reply.handle_reply(message, result)

    assert draft is not None
    assert draft.status == OutboundDraftStatus.PENDING
    notify.assert_called_once()
    assert notify.call_args.args[1] == "draft_created"


def test_handle_reply_variant_c_fires_urgent(account, vendor):
    thread = _thread(vendor)
    message = _inbound(account, thread)
    result = _classification(urgent=True)

    with (
        patch.object(reply, "call_llm_json", return_value=(_SLOTS, _FakeResponse())),
        patch.object(reply.notifications, "notify") as notify,
    ):
        draft = reply.handle_reply(message, result)

    assert draft is not None
    events = [call.args[1] for call in notify.call_args_list]
    assert "draft_created" in events
    assert "urgent_lead" in events


def test_handle_reply_blocked_escalates(account, vendor):
    thread = _thread(vendor)
    message = _inbound(account, thread)
    result = _classification()
    payload = {"slots": {**_SLOT_BODY, "main": "It will cost $5000."}}

    with (
        patch.object(reply, "call_llm_json", return_value=(payload, _FakeResponse())),
        patch.object(reply.notifications, "notify") as notify,
    ):
        draft = reply.handle_reply(message, result)

    assert draft is None
    assert AgentLog.objects.filter(thread=thread, event="reply_blocked").exists()
    notify.assert_called_once()
    assert notify.call_args.args[1] == "escalation"
