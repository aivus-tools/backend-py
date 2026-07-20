"""Tests for lead wiring and the inbound orchestrator (S3-25)."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from aivus_backend.core.enums import BriefSource
from aivus_backend.email_agent import classification
from aivus_backend.email_agent import tasks
from aivus_backend.email_agent.models import ActionAssignee
from aivus_backend.email_agent.models import ActionItem
from aivus_backend.email_agent.models import ActionItemStatus
from aivus_backend.email_agent.models import AgentLog
from aivus_backend.email_agent.models import EmailAccount
from aivus_backend.email_agent.models import EmailAccountRole
from aivus_backend.email_agent.models import EmailDirection
from aivus_backend.email_agent.models import EmailMessage
from aivus_backend.email_agent.models import EmailThread
from aivus_backend.email_agent.models import MessageIntent
from aivus_backend.email_agent.models import OutboundDraft
from aivus_backend.email_agent.models import ThreadState
from aivus_backend.email_agent.models import VendorAgentProfile
from aivus_backend.projects.models import Brief

pytestmark = pytest.mark.django_db

_ORDER_RAW = {
    "reasoning": "clear order",
    "intent": "order",
    "extracted": {"wants": "video", "deadline": "", "budget": "", "missing": ""},
    "action_items": [],
    "whos_ball": "agent",
    "safe_to_send": True,
    "escalate_reason": "",
    "pause_until": "",
    "language": "ru",
    "urgent": False,
    "confidence": 0.9,
}


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
    }
    defaults.update(over)
    return EmailThread.objects.create(**defaults)


def _inbound(account, thread, **over):
    defaults = {
        "account": account,
        "thread": thread,
        "provider_message_id": "<m1@client>",
        "direction": EmailDirection.IN,
        "from_email": "jane@client.com",
        "subject": "New project",
        "body_clean": "We need a corporate video.",
        "headers": {"from": "jane@client.com"},
    }
    defaults.update(over)
    return EmailMessage.objects.create(**defaults)


def test_wire_lead_creates_lead_for_order(account, vendor):
    thread = _thread(vendor)
    message = _inbound(account, thread)
    result = classification.coerce_classification(_ORDER_RAW)

    with patch(
        "aivus_backend.projects.api.views_brief_v3._enqueue_first_reply"
    ) as enqueue:
        brief = classification.wire_lead(message, result)

    assert brief is not None
    brief.refresh_from_db()
    assert brief.source == BriefSource.EMAIL
    assert brief.document_language == "ru"
    assert brief.pending_task_id != ""
    thread.refresh_from_db()
    assert thread.project_id is not None
    assert AgentLog.objects.filter(thread=thread, event="lead_created").exists()
    enqueue.assert_called_once()


def test_wire_lead_links_thread_attachments(account, vendor):
    from aivus_backend.email_agent import attachments
    from aivus_backend.email_agent.models import EmailAttachment

    thread = _thread(vendor)
    message = _inbound(account, thread)
    attachments.store_attachments(
        message,
        [
            {
                "filename": "brief.pdf",
                "content_type": "application/pdf",
                "payload": b"%PDF-1.4\ntrailer\n%%EOF\n",
            }
        ],
    )
    result = classification.coerce_classification(_ORDER_RAW)

    with patch("aivus_backend.projects.api.views_brief_v3._enqueue_first_reply"):
        brief = classification.wire_lead(message, result)

    assert brief is not None
    linked = EmailAttachment.objects.get(message=message)
    assert linked.brief_id == brief.id


def test_wire_lead_no_duplicate_on_existing_project(account, vendor):
    thread = _thread(vendor)
    message = _inbound(account, thread)
    result = classification.coerce_classification(_ORDER_RAW)

    first = classification.wire_lead(message, result)
    second = classification.wire_lead(
        _inbound(account, thread, provider_message_id="<m2@client>"), result
    )

    assert first is not None
    assert second is None
    assert Brief.objects.filter(source=BriefSource.EMAIL).count() == 1


@pytest.mark.parametrize("intent", ["question", "edits"])
def test_wire_lead_skips_pure_non_project_intents(account, vendor, intent):
    thread = _thread(vendor)
    message = _inbound(account, thread)
    result = classification.coerce_classification({**_ORDER_RAW, "intent": intent})

    assert classification.wire_lead(message, result) is None
    assert not Brief.objects.filter(source=BriefSource.EMAIL).exists()


def test_wire_lead_creates_on_follow_up_that_signals_project(account, vendor):
    # A follow_up that finally names a project (the client returns and asks for
    # the brief) becomes a lead — otherwise the vendor's brief link never gets
    # attached and every deferred client stays as a chase forever.
    thread = _thread(vendor)
    message = _inbound(account, thread)
    result = classification.coerce_classification({**_ORDER_RAW, "intent": "follow_up"})

    with patch("aivus_backend.projects.api.views_brief_v3._enqueue_first_reply"):
        brief = classification.wire_lead(message, result)

    assert brief is not None
    thread.refresh_from_db()
    assert thread.project_id is not None


def test_wire_lead_skips_follow_up_without_wants(account, vendor):
    thread = _thread(vendor)
    message = _inbound(account, thread)
    empty_extracted = {"wants": "", "deadline": "", "budget": "", "missing": ""}
    result = classification.coerce_classification(
        {**_ORDER_RAW, "intent": "follow_up", "extracted": empty_extracted}
    )

    assert classification.wire_lead(message, result) is None


def test_process_order_creates_lead_and_drafts(account, vendor):
    thread = _thread(vendor)
    message = _inbound(account, thread)
    result = classification.coerce_classification(_ORDER_RAW)

    with (
        patch.object(
            classification, "classify_message", return_value=(result, {"model": "x"})
        ),
        patch.object(tasks.reply, "handle_reply") as handle,
    ):
        outcome = tasks.process_inbound_message(str(message.id))

    assert outcome == "drafted"
    handle.assert_called_once()
    message.refresh_from_db()
    assert message.intent == MessageIntent.ORDER
    assert message.processed_at is not None
    thread.refresh_from_db()
    assert thread.project_id is not None


def test_process_is_idempotent_under_double_dispatch(account, vendor):
    thread = _thread(vendor)
    message = _inbound(account, thread)
    result = classification.coerce_classification(_ORDER_RAW)

    with (
        patch.object(
            classification, "classify_message", return_value=(result, {})
        ) as classify,
        patch.object(tasks.reply, "handle_reply"),
    ):
        first = tasks.process_inbound_message(str(message.id))
        second = tasks.process_inbound_message(str(message.id))

    assert first == "drafted"
    assert second == "already_claimed"
    assert classify.call_count == 1


def test_process_classify_failure_escalates_once(account, vendor):
    thread = _thread(vendor)
    message = _inbound(account, thread)

    with (
        patch.object(
            classification, "classify_message", side_effect=ValueError("bad json")
        ),
        patch.object(tasks.notifications, "notify") as notify,
    ):
        outcome = tasks.process_inbound_message(str(message.id))

    assert outcome == "classify_failed"
    assert AgentLog.objects.filter(thread=thread, event="classify_failed").exists()
    notify.assert_called_once()
    assert notify.call_args.args[1] == "escalation"
    assert notify.call_args.kwargs["dedup_key"] == f"classify_failed:{message.id}"


def test_process_low_confidence_escalates_without_lead(account, vendor):
    thread = _thread(vendor)
    message = _inbound(account, thread)
    result = classification.coerce_classification({**_ORDER_RAW, "confidence": 0.3})

    with (
        patch.object(classification, "classify_message", return_value=(result, {})),
        patch.object(tasks.notifications, "notify") as notify,
    ):
        outcome = tasks.process_inbound_message(str(message.id))

    assert outcome == "escalated"
    assert AgentLog.objects.filter(thread=thread, event="escalated").exists()
    notify.assert_called_once()


def test_process_junk_is_silent(account, vendor):
    thread = _thread(vendor)
    message = _inbound(account, thread)
    result = classification.coerce_classification({**_ORDER_RAW, "intent": "junk"})

    with (
        patch.object(classification, "classify_message", return_value=(result, {})),
        patch.object(tasks.notifications, "notify") as notify,
    ):
        outcome = tasks.process_inbound_message(str(message.id))

    assert outcome == "silent"
    notify.assert_not_called()
    assert not Brief.objects.filter(source=BriefSource.EMAIL).exists()


def test_process_gates_bulk_before_llm(account, vendor):
    thread = _thread(vendor)
    message = _inbound(account, thread, headers={"precedence": "bulk"})

    with patch.object(classification, "classify_message") as classify:
        outcome = tasks.process_inbound_message(str(message.id))

    assert outcome == "ignored:auto_or_bulk"
    classify.assert_not_called()


def test_process_out_of_office_pauses_without_llm(account, vendor):
    thread = _thread(vendor)
    message = _inbound(account, thread, headers={"auto-submitted": "auto-replied"})

    with (
        patch.object(classification, "classify_message") as classify,
        patch.object(tasks.triage.notifications, "notify"),
    ):
        outcome = tasks.process_inbound_message(str(message.id))

    assert outcome == "ooo"
    classify.assert_not_called()
    message.refresh_from_db()
    assert message.intent == MessageIntent.AUTO_REPLY


def test_a_re_promised_item_survives_the_orchestrator_close(account, vendor):
    thread = _thread(vendor)
    item = ActionItem.objects.create(
        thread=thread,
        assignee=ActionAssignee.CLIENT,
        text="send the raw footage",
        status=ActionItemStatus.OVERDUE,
    )
    message = _inbound(account, thread)
    result = classification.coerce_classification(
        {
            **_ORDER_RAW,
            "intent": "question",
            "action_items": [
                {"assignee": "client", "text": "send the raw footage", "due_at": ""}
            ],
            "fulfilled": [str(item.id)],
        }
    )

    with (
        patch.object(classification, "classify_message", return_value=(result, {})),
        patch.object(tasks.reply, "handle_reply"),
    ):
        tasks.process_inbound_message(str(message.id))

    item.refresh_from_db()
    assert item.status == ActionItemStatus.OVERDUE
    assert ActionItem.objects.filter(thread=thread).count() == 1


def test_a_tangential_client_reply_leaves_the_promise_open(account, vendor):
    thread = _thread(vendor)
    item = ActionItem.objects.create(
        thread=thread,
        assignee=ActionAssignee.CLIENT,
        text="send the raw footage",
        status=ActionItemStatus.OVERDUE,
    )
    message = _inbound(account, thread, body_clean="What format do you need?")
    result = classification.coerce_classification(
        {**_ORDER_RAW, "intent": "question", "whos_ball": "producer", "fulfilled": []}
    )

    with (
        patch.object(classification, "classify_message", return_value=(result, {})),
        patch.object(tasks.reply, "handle_reply"),
    ):
        tasks.process_inbound_message(str(message.id))

    item.refresh_from_db()
    assert item.status == ActionItemStatus.OVERDUE


def test_delivered_promise_is_closed_by_the_orchestrator(account, vendor):
    thread = _thread(vendor)
    item = ActionItem.objects.create(
        thread=thread,
        assignee=ActionAssignee.CLIENT,
        text="send the raw footage",
        status=ActionItemStatus.OVERDUE,
    )
    message = _inbound(account, thread, body_clean="Footage attached.")
    result = classification.coerce_classification(
        {**_ORDER_RAW, "intent": "follow_up", "fulfilled": [str(item.id)]}
    )

    with (
        patch.object(classification, "classify_message", return_value=(result, {})),
        patch.object(tasks.reply, "handle_reply"),
    ):
        tasks.process_inbound_message(str(message.id))

    item.refresh_from_db()
    assert item.status == ActionItemStatus.DONE


def test_pause_never_unblocks_drafting_on_a_taken_over_thread(account, vendor):
    VendorAgentProfile.objects.create(vendor=vendor, producer_email="prod@vendor.com")
    thread = _thread(vendor)
    message = _inbound(account, thread, from_email="prod@vendor.com")
    result = classification.coerce_classification(
        {**_ORDER_RAW, "intent": "question", "pause_until": "2099-01-01"}
    )

    with (
        patch.object(classification, "classify_message", return_value=(result, {})),
        patch.object(tasks.reply, "handle_reply") as handle,
    ):
        outcome = tasks.process_inbound_message(str(message.id))

    assert outcome == "silent"
    handle.assert_not_called()
    thread.refresh_from_db()
    assert thread.state == ThreadState.HUMAN_TAKEOVER
    assert thread.paused_until is not None


def test_spoofed_producer_address_does_not_take_over_the_thread(account, vendor):
    VendorAgentProfile.objects.create(vendor=vendor, producer_email="prod@vendor.com")
    thread = _thread(vendor)
    message = _inbound(
        account,
        thread,
        from_email="prod@vendor.com",
        headers={
            "from": "prod@vendor.com",
            "authentication-results": "mx.google.com; dmarc=fail header.from=x.com",
        },
    )
    result = classification.coerce_classification({**_ORDER_RAW, "intent": "question"})

    with (
        patch.object(classification, "classify_message", return_value=(result, {})),
        patch.object(tasks.reply, "handle_reply"),
    ):
        tasks.process_inbound_message(str(message.id))

    thread.refresh_from_db()
    assert thread.state != ThreadState.HUMAN_TAKEOVER
    assert not AgentLog.objects.filter(thread=thread, event="human_takeover").exists()


def test_genuine_client_email_resumes_a_paused_thread(account, vendor):
    thread = _thread(
        vendor,
        state=ThreadState.PAUSED,
        state_before_pause=ThreadState.ENGAGED,
        paused_until=timezone.now() + timedelta(days=5),
    )
    message = _inbound(account, thread)
    result = classification.coerce_classification({**_ORDER_RAW, "intent": "question"})

    with (
        patch.object(classification, "classify_message", return_value=(result, {})),
        patch.object(tasks.reply, "handle_reply"),
    ):
        tasks.process_inbound_message(str(message.id))

    thread.refresh_from_db()
    assert thread.state == ThreadState.ENGAGED
    assert thread.paused_until is None
    assert AgentLog.objects.filter(thread=thread, event="thread_resumed").exists()


def test_repeat_out_of_office_does_not_resume_the_thread(account, vendor):
    thread = _thread(
        vendor,
        state=ThreadState.PAUSED,
        paused_until=timezone.now() + timedelta(days=5),
    )
    message = _inbound(account, thread, headers={"auto-submitted": "auto-replied"})

    with patch.object(tasks.triage.notifications, "notify"):
        outcome = tasks.process_inbound_message(str(message.id))

    assert outcome == "ooo"
    thread.refresh_from_db()
    assert thread.state == ThreadState.PAUSED
    assert thread.paused_until is not None


def test_producer_reply_does_not_lift_the_client_pause(account, vendor):
    VendorAgentProfile.objects.create(vendor=vendor, producer_email="prod@vendor.com")
    thread = _thread(
        vendor,
        state=ThreadState.PAUSED,
        paused_until=timezone.now() + timedelta(days=5),
    )
    message = _inbound(account, thread, from_email="prod@vendor.com")
    result = classification.coerce_classification({**_ORDER_RAW, "intent": "question"})

    with patch.object(classification, "classify_message", return_value=(result, {})):
        tasks.process_inbound_message(str(message.id))

    thread.refresh_from_db()
    assert not AgentLog.objects.filter(thread=thread, event="thread_resumed").exists()


def test_producer_reply_triggers_human_takeover(account, vendor):
    VendorAgentProfile.objects.create(vendor=vendor, producer_email="prod@vendor.com")
    thread = _thread(vendor)
    message = _inbound(account, thread, from_email="prod@vendor.com")
    result = classification.coerce_classification({**_ORDER_RAW, "intent": "question"})

    with (
        patch.object(
            classification, "classify_message", return_value=(result, {})
        ) as classify,
        patch.object(tasks.reply, "handle_reply") as handle,
    ):
        outcome = tasks.process_inbound_message(str(message.id))

    assert outcome == "silent"
    classify.assert_called_once()
    handle.assert_not_called()
    thread.refresh_from_db()
    assert thread.state == ThreadState.HUMAN_TAKEOVER
    assert AgentLog.objects.filter(thread=thread, event="human_takeover").exists()
    assert not OutboundDraft.objects.filter(thread=thread).exists()
