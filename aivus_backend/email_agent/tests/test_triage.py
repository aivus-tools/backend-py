"""Tests for the pre-LLM triage gate (S3-23)."""

from unittest.mock import patch

import pytest

from aivus_backend.email_agent import triage
from aivus_backend.email_agent.models import AgentLog
from aivus_backend.email_agent.models import EmailAccount
from aivus_backend.email_agent.models import EmailAccountRole
from aivus_backend.email_agent.models import EmailDirection
from aivus_backend.email_agent.models import EmailMessage
from aivus_backend.email_agent.models import MessageIntent
from aivus_backend.email_agent.models import ThreadState

pytestmark = pytest.mark.django_db


@pytest.fixture
def account(vendor):
    return EmailAccount.objects.create(
        vendor=vendor,
        role=EmailAccountRole.MONITOR,
        email="monitor@vendor.com",
    )


def _thread(vendor, **over):
    from aivus_backend.email_agent.models import EmailThread

    defaults = {
        "vendor": vendor,
        "provider_thread_id": "t1",
        "client_email": "jane@client.com",
        "canonical_subject": "New project",
        "participants": ["jane@client.com", "ivan@vendor.com"],
    }
    defaults.update(over)
    return EmailThread.objects.create(**defaults)


def _inbound(account, thread, headers=None, **over):
    defaults = {
        "account": account,
        "thread": thread,
        "provider_message_id": "<m1@client>",
        "direction": EmailDirection.IN,
        "from_email": "jane@client.com",
        "headers": headers if headers is not None else {"from": "jane@client.com"},
    }
    defaults.update(over)
    return EmailMessage.objects.create(**defaults)


def test_normal_message_proceeds(account, vendor):
    thread = _thread(vendor)
    message = _inbound(account, thread)

    result = triage.pre_gate(message)

    assert result.proceed is True
    assert result.reason == ""


def test_self_message_by_agent_header_is_gated(account, vendor):
    thread = _thread(vendor)
    message = _inbound(account, thread, headers={"x-aivus-agent": str(vendor.id)})

    result = triage.pre_gate(message)

    assert result.proceed is False
    assert result.reason == triage.REASON_SELF


def test_self_message_by_known_outbound_id_is_gated(account, vendor):
    thread = _thread(vendor)
    EmailMessage.objects.create(
        account=account,
        thread=thread,
        provider_message_id="<out1@agent>",
        direction=EmailDirection.OUT,
        message_id_header="<out1@agent>",
    )
    message = _inbound(
        account,
        thread,
        provider_message_id="in-echo",
        headers={"message-id": "<out1@agent>"},
    )

    result = triage.pre_gate(message)

    assert result.proceed is False
    assert result.reason == triage.REASON_SELF


def test_bulk_precedence_is_gated(account, vendor):
    thread = _thread(vendor)
    message = _inbound(account, thread, headers={"precedence": "bulk"})

    result = triage.pre_gate(message)

    assert result.proceed is False
    assert result.reason == triage.REASON_AUTO_OR_BULK


def test_bounce_is_gated(account, vendor):
    thread = _thread(vendor)
    message = _inbound(account, thread, headers={"return-path": "<>"})

    result = triage.pre_gate(message)

    assert result.proceed is False
    assert result.reason == triage.REASON_AUTO_OR_BULK


def test_out_of_office_is_distinguished_from_bulk(account, vendor):
    thread = _thread(vendor)
    message = _inbound(account, thread, headers={"auto-submitted": "auto-replied"})

    result = triage.pre_gate(message)

    assert result.proceed is False
    assert result.reason == triage.REASON_OOO


def test_llm_cap_gates_after_threshold(account, vendor):
    thread = _thread(vendor)
    message = _inbound(account, thread)
    for _ in range(triage.THREAD_LLM_DAILY_CAP):
        AgentLog.objects.create(thread=thread, event="classified", payload={})

    result = triage.pre_gate(message)

    assert result.proceed is False
    assert result.reason == triage.REASON_LLM_CAP


def test_under_llm_cap_proceeds(account, vendor):
    thread = _thread(vendor)
    message = _inbound(account, thread)
    for _ in range(triage.THREAD_LLM_DAILY_CAP - 1):
        AgentLog.objects.create(thread=thread, event="classified", payload={})

    assert triage.pre_gate(message).proceed is True


def test_apply_ooo_pause_pauses_thread_and_notifies(account, vendor):
    thread = _thread(vendor)
    message = _inbound(account, thread, headers={"auto-submitted": "auto-replied"})

    with patch.object(triage.notifications, "notify") as notify:
        triage.apply_ooo_pause(message)

    message.refresh_from_db()
    thread.refresh_from_db()
    assert message.intent == MessageIntent.AUTO_REPLY
    assert message.is_auto_reply is True
    assert message.processed_at is not None
    assert thread.state == ThreadState.PAUSED
    assert thread.paused_until is not None
    assert AgentLog.objects.filter(thread=thread, event="ooo_paused").exists()
    notify.assert_called_once()
    assert notify.call_args.args[1] == "ooo_paused"
