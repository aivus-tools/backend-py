"""Tests for ingestion (thread stitching, dedup) and the polling tasks."""

from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from aivus_backend.email_agent import tasks
from aivus_backend.email_agent.ingest import ingest_parsed
from aivus_backend.email_agent.ingest import stitch_thread
from aivus_backend.email_agent.models import EmailAccount
from aivus_backend.email_agent.models import EmailAccountRole
from aivus_backend.email_agent.models import EmailAccountStatus
from aivus_backend.email_agent.models import EmailMessage
from aivus_backend.email_agent.models import EmailThread

pytestmark = pytest.mark.django_db


def _parsed(**over):
    base = {
        "from_email": "jane@client.com",
        "from_name": "Jane",
        "to_emails": ["agent@vendor.com"],
        "cc_emails": ["ivan@vendor.com"],
        "subject": "New project",
        "body_clean": "We need a corporate video.",
        "headers": {"from": "jane@client.com"},
        "message_id_header": "<m1@client>",
        "in_reply_to": "",
        "references": "",
        "canonical_subject": "New project",
        "attachments": [],
    }
    base.update(over)
    return base


@pytest.fixture
def account(vendor):
    return EmailAccount.objects.create(
        vendor=vendor,
        role=EmailAccountRole.MONITOR,
        email="monitor@vendor.com",
    )


def test_stitch_creates_new_thread(account):
    thread = stitch_thread(account, _parsed())
    assert thread.client_email == "jane@client.com"
    assert "jane@client.com" in thread.participants
    assert "ivan@vendor.com" in thread.participants


def test_stitch_matches_by_references(account):
    first = ingest_parsed(account, _parsed(message_id_header="<root@client>"), 1)
    assert first is not None
    reply = _parsed(
        message_id_header="<reply@client>",
        in_reply_to="<root@client>",
        references="<root@client>",
    )
    thread = stitch_thread(account, reply)
    assert thread.id == first.thread.id


def test_stitch_matches_by_subject_and_sender(account):
    thread = stitch_thread(account, _parsed(message_id_header="<a@client>"))
    EmailMessage.objects.create(
        account=account,
        thread=thread,
        provider_message_id="<a@client>",
        direction="in",
        message_id_header="<a@client>",
    )
    again = stitch_thread(account, _parsed(message_id_header="<b@client>"))
    assert again.id == thread.id
    assert EmailThread.objects.count() == 1


def test_store_dedups_by_message_id(account):
    first = ingest_parsed(account, _parsed(), 1)
    duplicate = ingest_parsed(account, _parsed(), 1)
    assert first is not None
    assert duplicate is None
    assert EmailMessage.objects.filter(account=account).count() == 1


def test_poll_account_ingests_and_advances_cursor(account):
    result = {
        "mode": "incremental",
        "uid_validity": "200",
        "last_uid": 7,
        "messages": [
            (6, _parsed(message_id_header="<m6@client>")),
            (7, _parsed(message_id_header="<m7@client>", subject="Re: New project")),
        ],
    }
    with (
        patch("aivus_backend.email_agent.mailbox.open_imap", return_value=MagicMock()),
        patch("aivus_backend.email_agent.mailbox.plan_sync", return_value=result),
        patch("aivus_backend.email_agent.tasks.process_inbound_message.delay") as proc,
    ):
        ingested = tasks.poll_account(str(account.id))

    assert ingested == 2
    assert proc.call_count == 2
    account.refresh_from_db()
    assert account.last_seen_uid == 7
    assert account.uid_validity == "200"
    assert account.next_poll_at is not None
    assert EmailMessage.objects.filter(account=account).count() == 2


def test_poll_account_auth_error_marks_expired(account):
    from aivus_backend.email_agent.mailbox import MailboxAuthError

    with (
        patch(
            "aivus_backend.email_agent.mailbox.open_imap",
            side_effect=MailboxAuthError("bad"),
        ),
        patch("aivus_backend.email_agent.tasks.notifications.notify") as notify,
    ):
        tasks.poll_account(str(account.id))

    account.refresh_from_db()
    assert account.status == EmailAccountStatus.EXPIRED
    notify.assert_called_once()
    assert notify.call_args.args[1] == "mailbox_disconnected"
    assert notify.call_args.kwargs["urgent"] is True


def test_dispatch_enqueues_due_accounts(account):
    with patch("aivus_backend.email_agent.tasks.poll_account.delay") as delay:
        count = tasks.dispatch_email_polls()

    assert count == 1
    delay.assert_called_once_with(str(account.id))
