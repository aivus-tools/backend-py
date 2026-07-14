"""Tests for the mini-CRM feed and follow-up dashboard (S3-38/39)."""

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from aivus_backend.email_agent import feed
from aivus_backend.email_agent.models import ActionAssignee
from aivus_backend.email_agent.models import ActionItem
from aivus_backend.email_agent.models import ActionItemStatus
from aivus_backend.email_agent.models import EmailAccount
from aivus_backend.email_agent.models import EmailAccountRole
from aivus_backend.email_agent.models import EmailDirection
from aivus_backend.email_agent.models import EmailMessage
from aivus_backend.email_agent.models import EmailThread
from aivus_backend.email_agent.models import OutboundDraft
from aivus_backend.email_agent.models import OutboundDraftKind
from aivus_backend.email_agent.models import OutboundDraftStatus
from aivus_backend.email_agent.models import ThreadState
from aivus_backend.email_agent.models import VendorAgentProfile

pytestmark = pytest.mark.django_db


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
        "canonical_subject": "New project",
        "participants": ["jane@client.com"],
        "memory": {"language": "en"},
    }
    defaults.update(over)
    return EmailThread.objects.create(**defaults)


def _message(account, thread, direction, **over):
    defaults = {
        "account": account,
        "thread": thread,
        "provider_message_id": f"<{direction}-{timezone.now().timestamp()}@x>",
        "direction": direction,
        "from_email": "jane@client.com",
    }
    defaults.update(over)
    return EmailMessage.objects.create(**defaults)


def test_feed_lists_vendor_threads_with_counts(account, vendor):
    thread = _thread(vendor)
    _message(account, thread, EmailDirection.IN)
    ActionItem.objects.create(
        thread=thread,
        assignee=ActionAssignee.CLIENT,
        text="send footage",
        status=ActionItemStatus.OVERDUE,
    )

    result = feed.list_threads(vendor, limit=25, offset=0)

    assert result["total"] == 1
    row = result["threads"][0]
    assert row["threadId"] == str(thread.id)
    assert row["overdueItemCount"] == 1
    assert row["needsAction"] is True


def test_feed_scopes_to_the_vendor(account, vendor, django_user_model):
    from aivus_backend.users.models import Vendor

    other_owner = django_user_model.objects.create_user(
        email="other@x.io", password="p@ss", name="Other", group="VENDOR"
    )
    other_vendor = Vendor.objects.create(name="Other", owner=other_owner)
    _thread(vendor)
    _thread(other_vendor, provider_thread_id="other")

    result = feed.list_threads(vendor, limit=25, offset=0)

    assert result["total"] == 1


def test_feed_sorts_action_needed_first(account, vendor):
    quiet = _thread(vendor, provider_thread_id="quiet", canonical_subject="quiet")
    _message(account, quiet, EmailDirection.IN)
    waiting = _thread(vendor, provider_thread_id="waiting", canonical_subject="waiting")
    OutboundDraft.objects.create(
        thread=waiting,
        kind=OutboundDraftKind.FIRST_REPLY,
        body="pending",
        status=OutboundDraftStatus.PENDING,
    )

    rows = feed.list_threads(vendor, limit=25, offset=0)["threads"]

    assert rows[0]["threadId"] == str(waiting.id)
    assert rows[0]["needsAction"] is True


def test_feed_pagination(account, vendor):
    for index in range(3):
        _thread(vendor, provider_thread_id=f"t-{index}")

    first = feed.list_threads(vendor, limit=2, offset=0)
    second = feed.list_threads(vendor, limit=2, offset=2)

    assert len(first["threads"]) == 2
    assert first["hasMore"] is True
    assert len(second["threads"]) == 1
    assert second["hasMore"] is False


def test_thread_without_project_reads_as_monitoring(account, vendor):
    _thread(vendor)
    row = feed.list_threads(vendor, limit=25, offset=0)["threads"][0]
    assert row["projectId"] is None
    assert row["state"] == ThreadState.MONITORING


def test_clamp_page_defaults_and_bounds():
    assert feed.clamp_page(None, None) == (feed.DEFAULT_PAGE_SIZE, 0)
    assert feed.clamp_page("5", "10") == (5, 10)
    assert feed.clamp_page("99999", "-3") == (feed.MAX_PAGE_SIZE, 0)
    assert feed.clamp_page("bad", "bad") == (feed.DEFAULT_PAGE_SIZE, 0)


def test_dashboard_surfaces_overdue_promise(account, vendor):
    thread = _thread(vendor)
    ActionItem.objects.create(
        thread=thread,
        assignee=ActionAssignee.CLIENT,
        text="send footage",
        status=ActionItemStatus.OVERDUE,
    )

    result = feed.list_followups(vendor)

    kinds = {item["kind"] for item in result["followups"]}
    assert feed.FOLLOWUP_OVERDUE_PROMISE in kinds


def test_dashboard_surfaces_stuck_approval(account, vendor):
    thread = _thread(vendor)
    draft = OutboundDraft.objects.create(
        thread=thread,
        kind=OutboundDraftKind.FIRST_REPLY,
        body="waiting",
        status=OutboundDraftStatus.PENDING,
    )

    result = feed.list_followups(vendor)

    stuck = [
        f for f in result["followups"] if f["kind"] == feed.FOLLOWUP_STUCK_APPROVAL
    ]
    assert len(stuck) == 1
    assert stuck[0]["draftId"] == str(draft.id)


def test_dashboard_surfaces_overdue_first_reply(account, vendor):
    thread = _thread(vendor)
    OutboundDraft.objects.create(
        thread=thread,
        kind=OutboundDraftKind.FIRST_REPLY,
        body="expired",
        status=OutboundDraftStatus.EXPIRED,
        metadata={"overdue": True},
    )

    result = feed.list_followups(vendor)

    kinds = {item["kind"] for item in result["followups"]}
    assert feed.FOLLOWUP_OVERDUE_FIRST_REPLY in kinds


def test_dashboard_surfaces_stale_thread(account, vendor):
    thread = _thread(vendor, state=ThreadState.ENGAGED)
    client_wrote = timezone.now() - timedelta(days=3, hours=1)
    agent_replied = timezone.now() - timedelta(days=3)
    inbound = _message(account, thread, EmailDirection.IN)
    outbound = _message(account, thread, EmailDirection.OUT)
    EmailMessage.objects.filter(id=inbound.id).update(created_at=client_wrote)
    EmailMessage.objects.filter(id=outbound.id).update(created_at=agent_replied)

    result = feed.list_followups(vendor)

    kinds = {item["kind"] for item in result["followups"]}
    assert feed.FOLLOWUP_STALE_THREAD in kinds


def test_dashboard_ignores_paused_promises(account, vendor):
    thread = _thread(vendor, state=ThreadState.PAUSED)
    ActionItem.objects.create(
        thread=thread,
        assignee=ActionAssignee.CLIENT,
        text="send footage",
        status=ActionItemStatus.OVERDUE,
    )

    result = feed.list_followups(vendor)

    kinds = {item["kind"] for item in result["followups"]}
    assert feed.FOLLOWUP_OVERDUE_PROMISE not in kinds


def test_prepare_followup_drafts_for_an_overdue_promise(account, vendor):
    VendorAgentProfile.objects.create(vendor=vendor, producer_email="prod@vendor.com")
    thread = _thread(vendor)
    _message(account, thread, EmailDirection.IN)
    ActionItem.objects.create(
        thread=thread,
        assignee=ActionAssignee.CLIENT,
        text="send footage",
        status=ActionItemStatus.OVERDUE,
        due_at=datetime(2020, 1, 1, tzinfo=UTC),
    )

    slots = {
        "slots": {
            "greeting": "Hi",
            "main": "Checking in.",
            "next_step": "",
            "signoff": "A",
        }
    }

    class _Resp:
        model_used = "gemini-3.1-pro-preview"
        input_tokens = 1
        output_tokens = 1
        cost_usd = 0.0
        latency_ms = 1

    from aivus_backend.email_agent import followup

    with patch.object(followup, "call_llm_json", return_value=(slots, _Resp())):
        draft = feed.prepare_followup(thread)

    assert draft.kind == OutboundDraftKind.FOLLOW_UP
    assert draft.status == OutboundDraftStatus.PENDING


def test_prepare_followup_rejects_when_nothing_is_overdue(account, vendor):
    thread = _thread(vendor)
    _message(account, thread, EmailDirection.IN)

    with pytest.raises(feed.FollowupError):
        feed.prepare_followup(thread)


def test_prepare_followup_rejects_a_paused_thread(account, vendor):
    thread = _thread(vendor, state=ThreadState.PAUSED)
    ActionItem.objects.create(
        thread=thread,
        assignee=ActionAssignee.CLIENT,
        text="send footage",
        status=ActionItemStatus.OVERDUE,
    )

    with pytest.raises(feed.FollowupError):
        feed.prepare_followup(thread)
