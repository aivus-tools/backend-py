"""Tests for thread memory and action-item tracking (S3-30/31)."""

from datetime import UTC
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from aivus_backend.email_agent import classification
from aivus_backend.email_agent import memory
from aivus_backend.email_agent.models import ActionAssignee
from aivus_backend.email_agent.models import ActionItem
from aivus_backend.email_agent.models import ActionItemStatus
from aivus_backend.email_agent.models import AgentLog
from aivus_backend.email_agent.models import EmailAccount
from aivus_backend.email_agent.models import EmailAccountRole
from aivus_backend.email_agent.models import EmailDirection
from aivus_backend.email_agent.models import EmailMessage
from aivus_backend.email_agent.models import EmailThread
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
    }
    defaults.update(over)
    return EmailMessage.objects.create(**defaults)


def _classification(**over):
    base = {
        "reasoning": "",
        "intent": "order",
        "extracted": {
            "wants": "video",
            "deadline": "Friday",
            "budget": "",
            "missing": "",
        },
        "action_items": [],
        "fulfilled": [],
        "whos_ball": "client",
        "safe_to_send": True,
        "escalate_reason": "",
        "pause_until": "",
        "language": "en",
        "urgent": False,
        "confidence": 0.9,
    }
    base.update(over)
    return classification.coerce_classification(base)


def test_persist_multiple_promises(account, vendor):
    thread = _thread(vendor)
    message = _inbound(account, thread)
    result = _classification(
        action_items=[
            {"assignee": "client", "text": "send footage", "due_at": ""},
            {"assignee": "producer", "text": "share estimate", "due_at": ""},
        ]
    )

    items = memory.persist_action_items(message, result)

    assert len(items) == 2
    assert ActionItem.objects.filter(thread=thread).count() == 2
    assert ActionItem.objects.filter(assignee=ActionAssignee.PRODUCER).exists()


def test_persist_parses_iso_due_with_offset(account, vendor):
    thread = _thread(vendor)
    message = _inbound(account, thread)
    result = _classification(
        action_items=[
            {
                "assignee": "client",
                "text": "reply",
                "due_at": "2026-08-01T10:00:00+02:00",
            }
        ]
    )

    item = memory.persist_action_items(message, result)[0]

    assert item.due_at == datetime(2026, 8, 1, 10, 0, tzinfo=ZoneInfo("Europe/Berlin"))


def test_persist_date_only_due_uses_vendor_timezone(account, vendor):
    VendorAgentProfile.objects.create(
        vendor=vendor, working_hours={"timezone": "America/New_York"}
    )
    thread = _thread(vendor)
    message = _inbound(account, thread)
    result = _classification(
        action_items=[{"assignee": "client", "text": "reply", "due_at": "2026-08-01"}]
    )

    item = memory.persist_action_items(message, result)[0]

    assert item.due_at is not None
    assert item.due_at.tzinfo == ZoneInfo("America/New_York")
    assert (item.due_at.hour, item.due_at.minute) == (23, 59)


def test_persist_logs_an_unparseable_deadline(account, vendor):
    thread = _thread(vendor)
    message = _inbound(account, thread)
    result = _classification(
        action_items=[{"assignee": "client", "text": "reply", "due_at": "next Friday"}]
    )

    item = memory.persist_action_items(message, result)[0]

    assert item.due_at is None
    assert AgentLog.objects.filter(thread=thread, event="due_unparsed").exists()


def test_persist_skips_invalid_rows(account, vendor):
    thread = _thread(vendor)
    message = _inbound(account, thread)
    result = _classification(
        action_items=[
            {"assignee": "nobody", "text": "x", "due_at": ""},
            {"assignee": "client", "text": "", "due_at": ""},
            {"assignee": "client", "text": "valid", "due_at": ""},
        ]
    )

    items = memory.persist_action_items(message, result)

    assert len(items) == 1
    assert items[0].text == "valid"


def test_update_memory_is_targeted_and_preserves_rest(account, vendor):
    thread = _thread(vendor, memory={"budget": "$5k", "note": "keep me"})
    result = _classification(
        extracted={
            "wants": "promo video",
            "deadline": "",
            "budget": "",
            "missing": "brief",
        }
    )

    memory.update_thread_memory(thread, result)

    thread.refresh_from_db()
    assert thread.memory["wants"] == "promo video"
    assert thread.memory["missing"] == "brief"
    assert thread.memory["budget"] == "$5k"
    assert thread.memory["note"] == "keep me"
    assert thread.memory["whos_ball"] == "client"


def test_dedupe_same_promise_updates_not_duplicates(account, vendor):
    thread = _thread(vendor)
    message = _inbound(account, thread)
    result = _classification(
        action_items=[
            {"assignee": "client", "text": "Send the footage files", "due_at": ""}
        ]
    )
    memory.persist_action_items(message, result)

    reworded = _classification(
        action_items=[
            {
                "assignee": "client",
                "text": "send the footage files",
                "due_at": "2026-08-01",
            }
        ]
    )
    memory.persist_action_items(
        _inbound(account, thread, provider_message_id="<m2>"), reworded
    )

    items = ActionItem.objects.filter(thread=thread, assignee=ActionAssignee.CLIENT)
    assert items.count() == 1
    updated = items.first()
    assert updated is not None
    assert updated.due_at is not None


def test_close_settles_only_the_promise_the_email_delivered(account, vendor):
    VendorAgentProfile.objects.create(vendor=vendor, producer_email="prod@vendor.com")
    thread = _thread(vendor)
    delivered = ActionItem.objects.create(
        thread=thread, assignee=ActionAssignee.CLIENT, text="send footage"
    )
    still_owed = ActionItem.objects.create(
        thread=thread, assignee=ActionAssignee.CLIENT, text="confirm the shoot date"
    )
    producer_item = ActionItem.objects.create(
        thread=thread, assignee=ActionAssignee.PRODUCER, text="share estimate"
    )
    agent_item = ActionItem.objects.create(
        thread=thread, assignee=ActionAssignee.AGENT, text="reply"
    )

    closed = memory.close_fulfilled_items(
        _inbound(account, thread), _classification(fulfilled=[str(delivered.id)])
    )

    assert closed == 1
    for item in (delivered, still_owed, producer_item, agent_item):
        item.refresh_from_db()
    assert delivered.status == ActionItemStatus.DONE
    assert still_owed.status == ActionItemStatus.OPEN
    assert producer_item.status == ActionItemStatus.OPEN
    assert agent_item.status == ActionItemStatus.OPEN


def test_close_settles_producer_items_on_producer_reply(account, vendor):
    VendorAgentProfile.objects.create(vendor=vendor, producer_email="prod@vendor.com")
    thread = _thread(vendor)
    producer_item = ActionItem.objects.create(
        thread=thread, assignee=ActionAssignee.PRODUCER, text="share estimate"
    )

    memory.close_fulfilled_items(
        _inbound(account, thread, from_email="prod@vendor.com"),
        _classification(fulfilled=[str(producer_item.id)]),
    )

    producer_item.refresh_from_db()
    assert producer_item.status == ActionItemStatus.DONE


def test_close_settles_an_overdue_item(account, vendor):
    thread = _thread(vendor)
    item = ActionItem.objects.create(
        thread=thread,
        assignee=ActionAssignee.CLIENT,
        text="send footage",
        status=ActionItemStatus.OVERDUE,
    )

    memory.close_fulfilled_items(
        _inbound(account, thread), _classification(fulfilled=[str(item.id)])
    )

    item.refresh_from_db()
    assert item.status == ActionItemStatus.DONE


def test_a_bare_reply_settles_nothing(account, vendor):
    thread = _thread(vendor)
    item = ActionItem.objects.create(
        thread=thread,
        assignee=ActionAssignee.CLIENT,
        text="send footage",
        status=ActionItemStatus.OVERDUE,
    )

    closed = memory.close_fulfilled_items(
        _inbound(account, thread), _classification(whos_ball="producer", fulfilled=[])
    )

    assert closed == 0
    item.refresh_from_db()
    assert item.status == ActionItemStatus.OVERDUE


def test_an_email_cannot_settle_the_other_party_promise(account, vendor):
    VendorAgentProfile.objects.create(vendor=vendor, producer_email="prod@vendor.com")
    thread = _thread(vendor)
    producer_item = ActionItem.objects.create(
        thread=thread, assignee=ActionAssignee.PRODUCER, text="share estimate"
    )

    closed = memory.close_fulfilled_items(
        _inbound(account, thread), _classification(fulfilled=[str(producer_item.id)])
    )

    assert closed == 0
    producer_item.refresh_from_db()
    assert producer_item.status == ActionItemStatus.OPEN


def test_spoofed_producer_address_is_treated_as_the_client(account, vendor):
    VendorAgentProfile.objects.create(vendor=vendor, producer_email="prod@vendor.com")
    thread = _thread(vendor)
    producer_item = ActionItem.objects.create(
        thread=thread, assignee=ActionAssignee.PRODUCER, text="share estimate"
    )
    spoofed = _inbound(
        account,
        thread,
        from_email="prod@vendor.com",
        headers={"authentication-results": "mx.google.com; dmarc=fail"},
    )

    assert memory.sender_party(spoofed) == ActionAssignee.CLIENT
    assert (
        memory.close_fulfilled_items(
            spoofed, _classification(fulfilled=[str(producer_item.id)])
        )
        == 0
    )


def test_hallucinated_fulfilled_id_settles_nothing(account, vendor):
    thread = _thread(vendor)
    item = ActionItem.objects.create(
        thread=thread, assignee=ActionAssignee.CLIENT, text="send footage"
    )

    closed = memory.close_fulfilled_items(
        _inbound(account, thread),
        _classification(fulfilled=["9f8ef0c4-0000-4000-8000-000000000000"]),
    )

    assert closed == 0
    item.refresh_from_db()
    assert item.status == ActionItemStatus.OPEN


def test_re_promised_item_survives_the_close(account, vendor):
    thread = _thread(vendor)
    message = _inbound(account, thread)
    promise = {"assignee": "client", "text": "send footage", "due_at": ""}
    tracked = memory.persist_action_items(
        message, _classification(action_items=[promise])
    )
    claimed_fulfilled = _classification(
        action_items=[promise], fulfilled=[str(tracked[0].id)]
    )

    closed = memory.close_fulfilled_items(
        message, claimed_fulfilled, exclude_ids=[item.id for item in tracked]
    )

    assert closed == 0
    tracked[0].refresh_from_db()
    assert tracked[0].status == ActionItemStatus.OPEN


def test_re_promise_with_a_new_deadline_reopens_and_resets_the_budget(account, vendor):
    thread = _thread(vendor)
    item = ActionItem.objects.create(
        thread=thread,
        assignee=ActionAssignee.CLIENT,
        text="send footage",
        status=ActionItemStatus.OVERDUE,
        due_at=datetime(2020, 1, 1, tzinfo=UTC),
        followup_count=2,
        last_followup_at=datetime(2020, 1, 2, tzinfo=UTC),
    )
    result = _classification(
        action_items=[
            {"assignee": "client", "text": "send footage", "due_at": "2099-01-01"}
        ]
    )

    memory.persist_action_items(_inbound(account, thread), result)

    item.refresh_from_db()
    assert item.status == ActionItemStatus.OPEN
    assert item.followup_count == 0
    assert item.last_followup_at is None
    assert ActionItem.objects.filter(thread=thread).count() == 1


def test_mark_overdue_items(account, vendor):
    thread = _thread(vendor)
    overdue = ActionItem.objects.create(
        thread=thread,
        assignee=ActionAssignee.CLIENT,
        text="late",
        due_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    future = ActionItem.objects.create(
        thread=thread,
        assignee=ActionAssignee.CLIENT,
        text="future",
        due_at=datetime(2099, 1, 1, tzinfo=UTC),
    )
    no_due = ActionItem.objects.create(
        thread=thread, assignee=ActionAssignee.CLIENT, text="no due"
    )

    changed = memory.mark_overdue_items(datetime(2026, 7, 14, tzinfo=UTC))

    assert changed == 1
    overdue.refresh_from_db()
    future.refresh_from_db()
    no_due.refresh_from_db()
    assert overdue.status == ActionItemStatus.OVERDUE
    assert future.status == ActionItemStatus.OPEN
    assert no_due.status == ActionItemStatus.OPEN
