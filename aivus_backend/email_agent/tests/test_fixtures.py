"""Email fixture corpus: parsing, triage and injection safety (S3-26)."""

from pathlib import Path

import pytest

from aivus_backend.email_agent import safety
from aivus_backend.email_agent import triage
from aivus_backend.email_agent.ingest import _participants
from aivus_backend.email_agent.mailbox import parse_raw_message
from aivus_backend.email_agent.models import EmailAccount
from aivus_backend.email_agent.models import EmailAccountRole
from aivus_backend.email_agent.models import EmailDirection
from aivus_backend.email_agent.models import EmailMessage
from aivus_backend.email_agent.models import EmailThread

pytestmark = pytest.mark.django_db

_FIXTURES = Path(__file__).parent / "fixtures"

_ALL = [
    "order.eml",
    "question.eml",
    "follow_up.eml",
    "edits.eml",
    "junk_spam.eml",
    "bulk_newsletter.eml",
    "bounce_dsn.eml",
    "ooo_auto_reply.eml",
    "prompt_injection.eml",
]


def _parse(name):
    return parse_raw_message((_FIXTURES / name).read_bytes())


@pytest.fixture
def account(vendor):
    return EmailAccount.objects.create(
        vendor=vendor,
        role=EmailAccountRole.AGENT,
        email="agent@vendor.com",
    )


def _message_from_fixture(account, vendor, name):
    parsed = _parse(name)
    thread = EmailThread.objects.create(
        vendor=vendor,
        provider_thread_id=name,
        client_email=parsed["from_email"],
        canonical_subject=parsed["canonical_subject"],
        participants=_participants(parsed),
    )
    return EmailMessage.objects.create(
        account=account,
        thread=thread,
        provider_message_id=parsed["message_id_header"] or name,
        direction=EmailDirection.IN,
        from_email=parsed["from_email"],
        to_emails=parsed["to_emails"],
        cc_emails=parsed["cc_emails"],
        subject=parsed["subject"],
        body_clean=parsed["body_clean"],
        headers=parsed["headers"],
    )


@pytest.mark.parametrize("name", _ALL)
def test_all_fixtures_parse(name):
    parsed = _parse(name)
    assert parsed["subject"]
    assert isinstance(parsed["headers"], dict)
    assert isinstance(parsed["to_emails"], list)


@pytest.mark.parametrize("name", ["bulk_newsletter.eml", "bounce_dsn.eml"])
def test_bulk_and_bounce_are_gated(account, vendor, name):
    message = _message_from_fixture(account, vendor, name)
    result = triage.pre_gate(message)
    assert result.proceed is False
    assert result.reason == triage.REASON_AUTO_OR_BULK


def test_ooo_is_gated_as_auto_reply(account, vendor):
    message = _message_from_fixture(account, vendor, "ooo_auto_reply.eml")
    result = triage.pre_gate(message)
    assert result.proceed is False
    assert result.reason == triage.REASON_OOO


@pytest.mark.parametrize("name", ["order.eml", "question.eml", "prompt_injection.eml"])
def test_useful_mail_proceeds(account, vendor, name):
    message = _message_from_fixture(account, vendor, name)
    assert triage.pre_gate(message).proceed is True


def test_injection_cannot_redirect_recipients(account, vendor):
    message = _message_from_fixture(account, vendor, "prompt_injection.eml")
    to, cc = safety.pin_recipients(
        message.thread.participants,
        producer_email="producer@vendor.com",
        agent_email=account.email,
    )
    recipients = " ".join(to + cc)
    assert "attacker@evil.com" in to
    assert "boss@evil.com" not in recipients
    assert "newrecipient@evil.com" not in recipients
    assert cc == ["producer@vendor.com"]


def test_injection_body_is_wrapped_as_untrusted(account, vendor):
    message = _message_from_fixture(account, vendor, "prompt_injection.eml")
    nonce, wrapped = safety.wrap_untrusted(message.body_clean)
    assert f'nonce="{nonce}"' in wrapped
    assert "Ignore all previous instructions" in wrapped


def test_injection_url_is_stripped_from_outbound(account, vendor):
    message = _message_from_fixture(account, vendor, "prompt_injection.eml")
    cleaned = safety.sanitize_outbound(
        f"Reply text with {message.body_clean}", allowed_urls=()
    )
    assert "phish.evil.com" not in cleaned
    assert "[link removed]" in cleaned


def test_mail_loop_self_detection(account, vendor):
    parsed = _parse("order.eml")
    thread = EmailThread.objects.create(
        vendor=vendor, provider_thread_id="loop", participants=[parsed["from_email"]]
    )
    message = EmailMessage.objects.create(
        account=account,
        thread=thread,
        provider_message_id="loop-in",
        direction=EmailDirection.IN,
        headers={"x-aivus-agent": str(vendor.id)},
    )
    result = triage.pre_gate(message)
    assert result.proceed is False
    assert result.reason == triage.REASON_SELF
