"""Notification events raised by the email agent, with their delivery classes.

Events are transport-independent: the dispatcher in ``notifications.py`` decides
which channel delivers them and whether a vendor's rules or working hours defer a
non-urgent one. Two independent policies decide that:

- INFO events (a plain useful inbound, an out-of-office pause) are the only ones
  ``urgent_and_digest`` mode may drop entirely — they are pure awareness;
- DEFERRABLE events wait for the next working-hours window instead of arriving at
  night. That is a superset of INFO: a promise deadline must always reach the
  producer, but it does not have to wake them.

Everything else (drafts, urgent leads, escalations, mailbox loss) goes out
immediately, regardless of mode or working hours. The daily digest has its own
schedule.
"""

from __future__ import annotations

from datetime import timedelta

from django.db import models


class NotificationEvent(models.TextChoices):
    DRAFT_CREATED = "draft_created", "Draft created"
    DRAFT_OVERDUE = "draft_overdue", "Draft overdue"
    URGENT_LEAD = "urgent_lead", "Urgent lead"
    ESCALATION = "escalation", "Escalation"
    MAILBOX_DISCONNECTED = "mailbox_disconnected", "Mailbox disconnected"
    OOO_PAUSED = "ooo_paused", "Out-of-office pause"
    INBOUND_EMAIL = "inbound_email", "Inbound email"
    DAILY_DIGEST = "daily_digest", "Daily digest"
    PROMISE_DUE = "promise_due", "Promise due"


INFO_EVENTS: frozenset[str] = frozenset(
    {
        NotificationEvent.INBOUND_EMAIL,
        NotificationEvent.OOO_PAUSED,
    }
)

DEFERRABLE_EVENTS: frozenset[str] = INFO_EVENTS | frozenset(
    {
        NotificationEvent.PROMISE_DUE,
    }
)

DEFAULT_DEDUP_WINDOW = timedelta(hours=6)

DEDUP_WINDOWS: dict[str, timedelta] = {
    NotificationEvent.DRAFT_CREATED: timedelta(hours=24),
    NotificationEvent.DRAFT_OVERDUE: timedelta(hours=24),
    NotificationEvent.URGENT_LEAD: timedelta(hours=24),
    NotificationEvent.ESCALATION: timedelta(hours=24),
    NotificationEvent.MAILBOX_DISCONNECTED: timedelta(hours=24),
    NotificationEvent.OOO_PAUSED: timedelta(hours=24),
    NotificationEvent.PROMISE_DUE: timedelta(hours=24),
    NotificationEvent.DAILY_DIGEST: timedelta(hours=20),
}


def dedup_window(event: str) -> timedelta:
    return DEDUP_WINDOWS.get(event, DEFAULT_DEDUP_WINDOW)


def is_deferrable(event: str) -> bool:
    return event in DEFERRABLE_EVENTS


def is_suppressible_by_mode(event: str) -> bool:
    return event in INFO_EVENTS
