"""Notification events raised by the email agent, with their delivery classes.

Events are transport-independent: the dispatcher in ``notifications.py`` decides
which channel delivers them and whether a vendor's rules or working hours defer a
non-urgent one. Delivery classes decide that policy:

- non-deferrable events (drafts, urgent leads, escalations, mailbox loss) always
  go out immediately, regardless of notification mode or working hours;
- INFO events (a plain useful inbound, an out-of-office pause) can be suppressed
  by ``urgent_and_digest`` mode and deferred outside working hours;
- the daily digest has its own schedule.
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

DEFAULT_DEDUP_WINDOW = timedelta(hours=6)

DEDUP_WINDOWS: dict[str, timedelta] = {
    NotificationEvent.DRAFT_CREATED: timedelta(hours=24),
    NotificationEvent.DRAFT_OVERDUE: timedelta(hours=24),
    NotificationEvent.URGENT_LEAD: timedelta(hours=24),
    NotificationEvent.ESCALATION: timedelta(hours=24),
    NotificationEvent.MAILBOX_DISCONNECTED: timedelta(hours=24),
    NotificationEvent.OOO_PAUSED: timedelta(hours=24),
    NotificationEvent.DAILY_DIGEST: timedelta(hours=20),
}


def dedup_window(event: str) -> timedelta:
    return DEDUP_WINDOWS.get(event, DEFAULT_DEDUP_WINDOW)


def is_deferrable(event: str) -> bool:
    return event in INFO_EVENTS
