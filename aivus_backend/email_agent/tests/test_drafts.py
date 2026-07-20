"""Tests for the draft-only autonomy layer and OutboundDraft lifecycle (S3-20/21)."""

from datetime import UTC
from datetime import datetime
from unittest.mock import patch

import pytest

from aivus_backend.email_agent import drafts
from aivus_backend.email_agent.models import AgentLog
from aivus_backend.email_agent.models import AutonomyMode
from aivus_backend.email_agent.models import EmailAccount
from aivus_backend.email_agent.models import EmailAccountRole
from aivus_backend.email_agent.models import EmailAccountStatus
from aivus_backend.email_agent.models import EmailDirection
from aivus_backend.email_agent.models import EmailMessage
from aivus_backend.email_agent.models import EmailThread
from aivus_backend.email_agent.models import OutboundDraft
from aivus_backend.email_agent.models import OutboundDraftKind
from aivus_backend.email_agent.models import OutboundDraftStatus
from aivus_backend.email_agent.models import ThreadState
from aivus_backend.email_agent.models import VendorAgentProfile

pytestmark = pytest.mark.django_db


class _SentMessage:
    provider_message_id = "<sent1@vendor.com>"


@pytest.fixture
def agent_account(vendor):
    return EmailAccount.objects.create(
        vendor=vendor,
        role=EmailAccountRole.AGENT,
        email="agent@vendor.com",
        status=EmailAccountStatus.CONNECTED,
    )


def _thread(vendor, **over):
    defaults = {
        "vendor": vendor,
        "provider_thread_id": "t1",
        "client_email": "jane@client.com",
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
        message_id_header="<m1@client>",
    )


def _draft(thread, inbound, **over):
    defaults = {
        "thread": thread,
        "in_reply_to_message": inbound,
        "kind": OutboundDraftKind.FIRST_REPLY,
        "body": "Hi, thanks for reaching out.",
        "status": OutboundDraftStatus.PENDING,
        "expires_at": datetime(2099, 1, 1, tzinfo=UTC),
        "metadata": {"variant": "A", "action": "acknowledge_receipt"},
    }
    defaults.update(over)
    return OutboundDraft.objects.create(**defaults)


def test_auto_send_never_enabled():
    profile = VendorAgentProfile(autonomy_mode=AutonomyMode.AUTO_SAFE)
    assert drafts.is_auto_send_enabled(None) is False
    assert drafts.is_auto_send_enabled(profile) is False


def test_approve_sends_and_marks_sent(agent_account, vendor):
    thread = _thread(vendor)
    inbound = _inbound(agent_account, thread)
    draft = _draft(thread, inbound)
    VendorAgentProfile.objects.create(vendor=vendor, producer_email="prod@vendor.com")

    with patch.object(drafts.sender, "send_reply", return_value=_SentMessage()) as send:
        sent = drafts.approve_draft(draft)

    assert sent.provider_message_id == "<sent1@vendor.com>"
    send.assert_called_once()
    draft.refresh_from_db()
    thread.refresh_from_db()
    assert draft.status == OutboundDraftStatus.SENT
    assert draft.provider_draft_id == "<sent1@vendor.com>"
    assert draft.metadata.get("edited") is None
    assert thread.state == ThreadState.ENGAGED
    assert AgentLog.objects.filter(thread=thread, event="draft_sent").exists()


def test_approve_with_edited_body_marks_edited(agent_account, vendor):
    thread = _thread(vendor)
    inbound = _inbound(agent_account, thread)
    draft = _draft(thread, inbound)

    with patch.object(drafts.sender, "send_reply", return_value=_SentMessage()) as send:
        drafts.approve_draft(draft, edited_body="Edited reply text.")

    assert send.call_args.args[2] == "Edited reply text."
    draft.refresh_from_db()
    assert draft.body == "Edited reply text."
    assert draft.metadata.get("edited") is True


def test_approve_non_pending_is_refused(agent_account, vendor):
    thread = _thread(vendor)
    inbound = _inbound(agent_account, thread)
    draft = _draft(thread, inbound, status=OutboundDraftStatus.SENT)

    with pytest.raises(drafts.DraftError):
        drafts.approve_draft(draft)


def test_approve_expired_is_refused(agent_account, vendor):
    thread = _thread(vendor)
    inbound = _inbound(agent_account, thread)
    draft = _draft(thread, inbound, expires_at=datetime(2000, 1, 1, tzinfo=UTC))

    with pytest.raises(drafts.DraftError):
        drafts.approve_draft(draft)


def test_approve_without_agent_mailbox_is_refused(vendor):
    thread = _thread(vendor)
    monitor = EmailAccount.objects.create(
        vendor=vendor, role=EmailAccountRole.MONITOR, email="mon@vendor.com"
    )
    inbound = _inbound(monitor, thread)
    draft = _draft(thread, inbound)

    with pytest.raises(drafts.DraftError):
        drafts.approve_draft(draft)


def test_edit_updates_body_and_marks_edited(agent_account, vendor):
    thread = _thread(vendor)
    inbound = _inbound(agent_account, thread)
    draft = _draft(thread, inbound)

    drafts.edit_draft(draft, "New body")

    draft.refresh_from_db()
    assert draft.body == "New body"
    assert draft.metadata.get("edited") is True
    assert draft.status == OutboundDraftStatus.PENDING


def test_reject_discards_draft(agent_account, vendor):
    thread = _thread(vendor)
    inbound = _inbound(agent_account, thread)
    draft = _draft(thread, inbound)

    drafts.reject_draft(draft)

    draft.refresh_from_db()
    assert draft.status == OutboundDraftStatus.REJECTED
    assert AgentLog.objects.filter(thread=thread, event="draft_rejected").exists()


def test_expire_first_reply_re_notifies_and_flags_overdue(agent_account, vendor):
    thread = _thread(vendor)
    inbound = _inbound(agent_account, thread)
    draft = _draft(thread, inbound, expires_at=datetime(2000, 1, 1, tzinfo=UTC))
    now = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)

    with patch.object(drafts.notifications, "notify") as notify:
        expired = drafts.expire_stale_drafts(now)

    assert expired == 1
    draft.refresh_from_db()
    assert draft.status == OutboundDraftStatus.EXPIRED
    assert draft.metadata.get("overdue") is True
    notify.assert_called_once()
    assert notify.call_args.args[1] == "draft_overdue"


def test_expire_non_first_reply_does_not_notify(agent_account, vendor):
    thread = _thread(vendor)
    inbound = _inbound(agent_account, thread)
    draft = _draft(
        thread,
        inbound,
        kind=OutboundDraftKind.OTHER,
        expires_at=datetime(2000, 1, 1, tzinfo=UTC),
    )
    now = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)

    with patch.object(drafts.notifications, "notify") as notify:
        drafts.expire_stale_drafts(now)

    draft.refresh_from_db()
    assert draft.status == OutboundDraftStatus.EXPIRED
    assert draft.metadata.get("overdue") is None
    notify.assert_not_called()


def test_expire_skips_future_drafts(agent_account, vendor):
    thread = _thread(vendor)
    inbound = _inbound(agent_account, thread)
    draft = _draft(thread, inbound, expires_at=datetime(2099, 1, 1, tzinfo=UTC))
    now = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)

    with patch.object(drafts.notifications, "notify"):
        expired = drafts.expire_stale_drafts(now)

    assert expired == 0
    draft.refresh_from_db()
    assert draft.status == OutboundDraftStatus.PENDING
