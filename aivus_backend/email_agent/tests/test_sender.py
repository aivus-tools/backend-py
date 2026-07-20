"""Tests for the outbound reply builder and sender."""

import uuid
from datetime import UTC
from datetime import datetime
from unittest.mock import patch

import pytest

from aivus_backend.email_agent import sender
from aivus_backend.email_agent.mailbox import MailboxError
from aivus_backend.email_agent.models import EmailAccount
from aivus_backend.email_agent.models import EmailAccountRole
from aivus_backend.email_agent.models import EmailMessage
from aivus_backend.email_agent.models import EmailThread


def _agent_account():
    return EmailAccount(
        role=EmailAccountRole.AGENT,
        email="agent@vendor.com",
        vendor_id=uuid.uuid4(),
    )


def _thread():
    return EmailThread(
        participants=["jane@client.com", "agent@vendor.com", "ivan@vendor.com"],
        canonical_subject="New project",
    )


def test_build_reply_mime_pins_recipients_and_headers():
    account = _agent_account()
    parent = EmailMessage(message_id_header="<p@client>", references="<root@client>")

    mime = sender.build_reply_mime(
        account,
        _thread(),
        "Thanks! http://evil.com/track",
        producer_email="ivan@vendor.com",
        parent=parent,
    )

    assert mime["To"] == "jane@client.com"
    assert mime["Cc"] == "ivan@vendor.com"
    assert mime["Subject"] == "Re: New project"
    assert mime["X-Aivus-Agent"] == str(account.vendor_id)
    assert mime["Auto-Submitted"] == "auto-replied"
    assert mime["In-Reply-To"] == "<p@client>"
    assert "<root@client>" in mime["References"]
    assert "<p@client>" in mime["References"]
    assert "[link removed]" in mime.get_content()


def test_build_reply_mime_appends_quoted_history():
    account = _agent_account()
    parent = EmailMessage(
        message_id_header="<p@client>",
        from_email="jane@client.com",
        body_clean=(
            "Please share the portfolio at https://vilka.co.\nAlso, budget is ~5k."
        ),
    )
    parent.created_at = datetime(2026, 7, 19, 15, 3, tzinfo=UTC)

    mime = sender.build_reply_mime(
        account,
        _thread(),
        "Sure, sending over.",
        producer_email="ivan@vendor.com",
        parent=parent,
    )

    body = mime.get_content()
    assert "Sure, sending over." in body
    assert "On 19 Jul 2026 at 15:03 UTC, jane@client.com wrote:" in body
    assert "> Please share the portfolio at https://vilka.co." in body
    # Quoted history is NOT run through sanitize_outbound — the client's own
    # URL is preserved verbatim so the vendor sees exactly what was said.
    assert "https://vilka.co" in body


def test_build_reply_mime_omits_quote_when_no_parent():
    account = _agent_account()

    mime = sender.build_reply_mime(
        account,
        _thread(),
        "Hi there.",
        producer_email="ivan@vendor.com",
        parent=None,
    )

    body = mime.get_content()
    assert body.strip() == "Hi there."
    assert "wrote:" not in body


def test_monitor_mailbox_cannot_send():
    account = EmailAccount(
        role=EmailAccountRole.MONITOR,
        email="info@vendor.com",
        vendor_id=uuid.uuid4(),
    )
    with pytest.raises(MailboxError):
        sender.send_reply(account, _thread(), "hi", producer_email="ivan@vendor.com")


@pytest.mark.django_db
def test_send_reply_records_outbound(vendor):
    account = EmailAccount.objects.create(
        vendor=vendor, role=EmailAccountRole.AGENT, email="agent@vendor.com"
    )
    thread = EmailThread.objects.create(
        vendor=vendor,
        provider_thread_id="t1",
        participants=["jane@client.com", "agent@vendor.com", "ivan@vendor.com"],
        canonical_subject="New project",
    )

    with patch(
        "aivus_backend.email_agent.sender.smtp_send_raw",
        return_value="<out@agent>",
    ):
        record = sender.send_reply(
            account,
            thread,
            "Thanks for reaching out.",
            producer_email="ivan@vendor.com",
        )

    assert record.direction == "out"
    assert record.message_id_header == "<out@agent>"
    assert record.to_emails == ["jane@client.com"]
    assert record.cc_emails == ["ivan@vendor.com"]
