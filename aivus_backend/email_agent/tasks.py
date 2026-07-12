"""Celery tasks for the email agent: fan-out polling and per-account ingestion."""

from __future__ import annotations

import contextlib
from datetime import timedelta

from celery import shared_task
from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from aivus_backend.email_agent import mailbox
from aivus_backend.email_agent.ingest import ingest_parsed
from aivus_backend.email_agent.models import EmailAccount
from aivus_backend.email_agent.models import EmailAccountStatus

POLL_INTERVAL_SECONDS = 60
LOCK_TTL_SECONDS = 300


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
            return 0

        try:
            result = mailbox.plan_sync(account, client)
        finally:
            with contextlib.suppress(Exception):
                client.logout()

        ingested = 0
        for uid, parsed in result["messages"]:
            if ingest_parsed(account, parsed, uid) is not None:
                ingested += 1

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
        return ingested
    finally:
        cache.delete(lock_key)
