"""Celery tasks for the email agent: fan-out polling and per-account ingestion."""

from __future__ import annotations

import contextlib
from datetime import timedelta

from celery import shared_task
from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from aivus_backend.email_agent import classification
from aivus_backend.email_agent import mailbox
from aivus_backend.email_agent import notifications
from aivus_backend.email_agent import reply
from aivus_backend.email_agent import triage
from aivus_backend.email_agent.events import NotificationEvent
from aivus_backend.email_agent.ingest import ingest_parsed
from aivus_backend.email_agent.models import AgentLog
from aivus_backend.email_agent.models import EmailAccount
from aivus_backend.email_agent.models import EmailAccountStatus
from aivus_backend.email_agent.models import EmailDirection
from aivus_backend.email_agent.models import EmailMessage

POLL_INTERVAL_SECONDS = 60
LOCK_TTL_SECONDS = 300
UNPROCESSED_SWEEP_LAG = timedelta(minutes=10)


@shared_task
def process_inbound_message(message_id: str) -> str:
    """Classify one inbound message and route it: ignore, escalate, or draft.

    A single atomic claim on ``processed_at`` makes this exactly-once at the DB
    level (the default cache is fail-open, so a Redis lock would not do). An LLM
    failure is terminal — ``call_llm`` already retries and falls back internally
    — and escalates once to the producer via the notify dedup key.
    """
    claimed = EmailMessage.objects.filter(
        id=message_id,
        direction=EmailDirection.IN,
        processed_at__isnull=True,
    ).update(processed_at=timezone.now())
    if not claimed:
        return "already_claimed"

    message = EmailMessage.objects.select_related("thread", "thread__vendor").get(
        id=message_id
    )
    thread = message.thread
    vendor = thread.vendor

    gate = triage.pre_gate(message)
    if not gate.proceed:
        if gate.reason == triage.REASON_OOO:
            triage.apply_ooo_pause(message)
            return "ooo"
        AgentLog.objects.create(
            thread=thread, event="ignored", payload={"reason": gate.reason}
        )
        return f"ignored:{gate.reason}"

    try:
        result, trace = classification.classify_message(message)
    except (ValueError, RuntimeError) as exc:
        AgentLog.objects.create(
            thread=thread, event="classify_failed", payload={"error": str(exc)}
        )
        notifications.notify(
            vendor,
            NotificationEvent.ESCALATION,
            {"lines": [f"Subject: {message.subject}"]},
            dedup_key=f"classify_failed:{message_id}",
        )
        return "classify_failed"

    classification.apply_classification(message, result, trace)
    classification.wire_lead(message, result)

    decision = classification.reply_decision(message, result)
    if decision == classification.DECISION_SILENT:
        return "silent"
    if decision == classification.DECISION_ESCALATE:
        notifications.notify(
            vendor,
            NotificationEvent.ESCALATION,
            {
                "lines": [
                    f"Subject: {message.subject}",
                    f"Reason: {result.escalate_reason or 'low_confidence'}",
                ]
            },
            dedup_key=f"escalation:{message_id}",
        )
        AgentLog.objects.create(
            thread=thread,
            event="escalated",
            payload={"reason": result.escalate_reason},
        )
        return "escalated"

    reply.handle_reply(message, result)
    return "drafted"


def _dispatch_unprocessed(account: EmailAccount) -> None:
    """Re-dispatch inbound messages a crashed poll ingested but never handed off.

    Fresh messages are dispatched inline; this sweep only picks up ones older
    than the lag, so it never races the in-flight tasks. The DB claim makes a
    double dispatch harmless.
    """
    stale = EmailMessage.objects.filter(
        account=account,
        direction=EmailDirection.IN,
        processed_at__isnull=True,
        created_at__lt=timezone.now() - UNPROCESSED_SWEEP_LAG,
    ).values_list("id", flat=True)
    for message_id in stale:
        process_inbound_message.delay(str(message_id))


@shared_task
def flush_deferred_notifications() -> int:
    """Beat entry: deliver notifications whose working-hours window has opened."""
    return notifications.flush_due_notifications(timezone.now())


@shared_task
def send_daily_digests() -> int:
    """Beat entry: send each vendor its daily digest at their local digest hour."""
    return notifications.dispatch_due_digests(timezone.now())


@shared_task
def dispatch_email_polls() -> int:
    """Beat entry: enqueue a poll for every account that is due."""
    now = timezone.now()
    due = EmailAccount.objects.filter(
        status=EmailAccountStatus.CONNECTED,
        deleted_at__isnull=True,
    ).filter(Q(next_poll_at__lte=now) | Q(next_poll_at__isnull=True))
    account_ids = list(due.values_list("id", flat=True))
    for account_id in account_ids:
        poll_account.delay(str(account_id))
    return len(account_ids)


@shared_task
def poll_account(account_id: str) -> int:
    """Poll one mailbox, ingest new messages, and advance the sync cursor.

    Serialized per account by a short-lived Redis lock so overlapping beats do
    not double-fetch. A rejected login marks the account expired; a transient
    failure just leaves next_poll_at unchanged so the next beat retries it.
    """
    lock_key = f"email_poll:{account_id}"
    if not cache.add(lock_key, "1", LOCK_TTL_SECONDS):
        return 0
    try:
        account = EmailAccount.objects.filter(
            id=account_id,
            status=EmailAccountStatus.CONNECTED,
            deleted_at__isnull=True,
        ).first()
        if account is None:
            return 0

        try:
            client = mailbox.open_imap(account)
        except mailbox.MailboxAuthError:
            account.status = EmailAccountStatus.EXPIRED
            account.save(update_fields=["status", "updated_at"])
            notifications.notify(
                account.vendor,
                NotificationEvent.MAILBOX_DISCONNECTED,
                {"lines": [f"Mailbox: {account.email}"]},
                urgent=True,
                dedup_key=str(account.id),
            )
            return 0

        try:
            result = mailbox.plan_sync(account, client)
        finally:
            with contextlib.suppress(Exception):
                client.logout()

        ingested = 0
        fresh_ids: list[str] = []
        for uid, parsed in result["messages"]:
            message = ingest_parsed(account, parsed, uid)
            if message is not None:
                ingested += 1
                fresh_ids.append(str(message.id))

        account.uid_validity = result["uid_validity"]
        account.last_seen_uid = result["last_uid"]
        account.last_synced_at = timezone.now()
        account.next_poll_at = timezone.now() + timedelta(seconds=POLL_INTERVAL_SECONDS)
        account.save(
            update_fields=[
                "uid_validity",
                "last_seen_uid",
                "last_synced_at",
                "next_poll_at",
                "updated_at",
            ]
        )

        for message_id in fresh_ids:
            process_inbound_message.delay(message_id)
        _dispatch_unprocessed(account)
        return ingested
    finally:
        cache.delete(lock_key)
