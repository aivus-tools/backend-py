"""Mini-CRM read models: the thread feed and the follow-up dashboard (S3-38/39).

Read-only views over the data the agent already tracks. The feed is the vendor's
inbox of conversations, sorted so anything waiting on them floats up; the
dashboard answers "who is stuck" — promises past due, drafts waiting for
approval, threads gone quiet, and first replies that expired unapproved. The
"prepare a follow-up" action reuses the same engine the beat sweep uses, so a
manual nudge and an automatic one are identical drafts.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from django.db.models import Count
from django.db.models import F
from django.db.models import Max
from django.db.models import Q
from django.utils import timezone

from aivus_backend.email_agent.models import ActionAssignee
from aivus_backend.email_agent.models import ActionItemStatus
from aivus_backend.email_agent.models import EmailDirection
from aivus_backend.email_agent.models import EmailThread
from aivus_backend.email_agent.models import OutboundDraft
from aivus_backend.email_agent.models import OutboundDraftKind
from aivus_backend.email_agent.models import OutboundDraftStatus
from aivus_backend.email_agent.models import ThreadState

if TYPE_CHECKING:
    from datetime import datetime

    from aivus_backend.users.models import Vendor

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100
STALE_THREAD_AFTER = timedelta(days=2)
DASHBOARD_LIMIT = 100

FOLLOWUP_OVERDUE_PROMISE = "overdue_promise"
FOLLOWUP_STUCK_APPROVAL = "stuck_approval"
FOLLOWUP_STALE_THREAD = "stale_thread"
FOLLOWUP_OVERDUE_FIRST_REPLY = "overdue_first_reply"


def _iso_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _thread_feed_queryset(vendor: Vendor):
    return (
        EmailThread.objects.filter(vendor=vendor, deleted_at__isnull=True)
        .select_related("project")
        .annotate(
            pending_drafts=Count(
                "drafts",
                filter=Q(drafts__status=OutboundDraftStatus.PENDING),
                distinct=True,
            ),
            overdue_items=Count(
                "action_items",
                filter=Q(action_items__status=ActionItemStatus.OVERDUE),
                distinct=True,
            ),
            open_items=Count(
                "action_items",
                filter=Q(action_items__status=ActionItemStatus.OPEN),
                distinct=True,
            ),
            last_message_at=Max("messages__created_at"),
        )
    )


def serialize_thread_summary(thread: EmailThread) -> dict:
    """One row of the CRM feed. A thread with no project reads as monitoring.

    The counts come off ``_thread_feed_queryset`` annotations; ``getattr`` keeps
    the serializer usable on a plain instance too.
    """
    project = thread.project
    pending = getattr(thread, "pending_drafts", 0)
    overdue = getattr(thread, "overdue_items", 0)
    open_items = getattr(thread, "open_items", 0)
    last_activity = getattr(thread, "last_message_at", None) or thread.created_at
    needs_action = bool(
        pending or overdue or thread.state == ThreadState.HUMAN_TAKEOVER
    )
    return {
        "threadId": str(thread.id),
        "clientEmail": thread.client_email,
        "clientName": thread.client_name,
        "subject": thread.canonical_subject,
        "state": thread.state,
        "projectId": str(project.id) if project is not None else None,
        "projectName": project.name if project is not None else "",
        "needsAction": needs_action,
        "pendingDraftCount": pending,
        "overdueItemCount": overdue,
        "openItemCount": open_items,
        "lastActivityAt": last_activity.isoformat(),
    }


def list_threads(vendor: Vendor, *, limit: int, offset: int) -> dict:
    """A page of the vendor's threads, action-needed first, then most recent."""
    queryset = _thread_feed_queryset(vendor).order_by(
        "-pending_drafts",
        "-overdue_items",
        "-last_message_at",
        "-created_at",
    )
    total = queryset.count()
    page = list(queryset[offset : offset + limit])
    return {
        "threads": [serialize_thread_summary(thread) for thread in page],
        "total": total,
        "limit": limit,
        "offset": offset,
        "hasMore": offset + len(page) < total,
    }


def clamp_page(limit: str | None, offset: str | None) -> tuple[int, int]:
    """Coerce raw query params into a sane (limit, offset)."""
    try:
        limit_value = int(limit) if limit is not None else DEFAULT_PAGE_SIZE
    except (TypeError, ValueError):
        limit_value = DEFAULT_PAGE_SIZE
    try:
        offset_value = int(offset) if offset is not None else 0
    except (TypeError, ValueError):
        offset_value = 0
    limit_value = max(1, min(limit_value, MAX_PAGE_SIZE))
    offset_value = max(0, offset_value)
    return limit_value, offset_value


def _overdue_promise_followups(vendor: Vendor, now: datetime) -> list[dict]:
    # Only a CLIENT-owed overdue promise belongs here: the card is labelled as the
    # client's and the "prepare follow-up" action only chases client promises, so a
    # producer-owed overdue item would mislabel and offer an action that always
    # 409s. A thread that already has a pending draft is dropped, mirroring the
    # sweep's guard, so the card and its action disappear once a follow-up exists.
    threads = (
        EmailThread.objects.filter(
            vendor=vendor,
            deleted_at__isnull=True,
            action_items__status=ActionItemStatus.OVERDUE,
            action_items__assignee=ActionAssignee.CLIENT,
        )
        .exclude(state__in=(ThreadState.PAUSED, ThreadState.HUMAN_TAKEOVER))
        .exclude(drafts__status=OutboundDraftStatus.PENDING)
        .distinct()[:DASHBOARD_LIMIT]
    )
    return [
        {
            "kind": FOLLOWUP_OVERDUE_PROMISE,
            "threadId": str(thread.id),
            "subject": thread.canonical_subject,
            "clientEmail": thread.client_email,
            "detail": "A client promise is past due.",
        }
        for thread in threads
    ]


def _stuck_approval_followups(vendor: Vendor, now: datetime) -> list[dict]:
    drafts = (
        OutboundDraft.objects.filter(
            thread__vendor=vendor,
            status=OutboundDraftStatus.PENDING,
        )
        .select_related("thread")
        .order_by("created_at")[:DASHBOARD_LIMIT]
    )
    return [
        {
            "kind": FOLLOWUP_STUCK_APPROVAL,
            "threadId": str(draft.thread_id),
            "draftId": str(draft.id),
            "subject": draft.thread.canonical_subject,
            "clientEmail": draft.thread.client_email,
            "detail": "A drafted reply is waiting for your approval.",
            "since": draft.created_at.isoformat(),
        }
        for draft in drafts
    ]


def _overdue_first_reply_followups(vendor: Vendor, now: datetime) -> list[dict]:
    drafts = (
        OutboundDraft.objects.filter(
            thread__vendor=vendor,
            kind=OutboundDraftKind.FIRST_REPLY,
            status=OutboundDraftStatus.EXPIRED,
            metadata__overdue=True,
        )
        .select_related("thread")
        .order_by("-updated_at")[:DASHBOARD_LIMIT]
    )
    return [
        {
            "kind": FOLLOWUP_OVERDUE_FIRST_REPLY,
            "threadId": str(draft.thread_id),
            "subject": draft.thread.canonical_subject,
            "clientEmail": draft.thread.client_email,
            "detail": "A first-reply draft expired before it was approved.",
        }
        for draft in drafts
    ]


def _stale_thread_followups(vendor: Vendor, now: datetime) -> list[dict]:
    cutoff = now - STALE_THREAD_AFTER
    threads = (
        EmailThread.objects.filter(
            vendor=vendor,
            deleted_at__isnull=True,
            state=ThreadState.ENGAGED,
        )
        .annotate(
            last_at=Max("messages__created_at"),
            last_inbound_at=Max(
                "messages__created_at",
                filter=Q(messages__direction=EmailDirection.IN),
            ),
            last_outbound_at=Max(
                "messages__created_at",
                filter=Q(messages__direction=EmailDirection.OUT),
            ),
        )
        .filter(last_at__lte=cutoff, last_outbound_at__isnull=False)
        .filter(
            Q(last_inbound_at__isnull=True)
            | Q(last_outbound_at__gt=F("last_inbound_at"))
        )[:DASHBOARD_LIMIT]
    )
    return [
        {
            "kind": FOLLOWUP_STALE_THREAD,
            "threadId": str(thread.id),
            "subject": thread.canonical_subject,
            "clientEmail": thread.client_email,
            "detail": "No reply from the client for two days.",
            "since": _iso_or_none(getattr(thread, "last_at", None)),
        }
        for thread in threads
    ]


def list_followups(vendor: Vendor, now: datetime | None = None) -> dict:
    """The "who is stuck" dashboard: everything waiting on a nudge, by bucket."""
    now = now or timezone.now()
    items = [
        *_overdue_promise_followups(vendor, now),
        *_stuck_approval_followups(vendor, now),
        *_overdue_first_reply_followups(vendor, now),
        *_stale_thread_followups(vendor, now),
    ]
    return {"followups": items, "total": len(items)}


class FollowupError(Exception):
    """A manual follow-up could not be prepared; the message is vendor-facing."""


def prepare_followup(thread: EmailThread) -> OutboundDraft:
    """Draft a follow-up for a thread's overdue client promises, on demand.

    The same engine the beat sweep uses, so a manual nudge is identical to an
    automatic one — including the guardrails (commitment blacklist, sanitizer, the
    silenced-thread and existing-draft checks). Raises when there is nothing to
    chase or the thread cannot accept one.
    """
    from aivus_backend.email_agent import followup  # noqa: PLC0415

    now = timezone.now()
    if not followup.accepts_client_followup(thread, now):
        msg = "This thread cannot take a follow-up right now."
        raise FollowupError(msg)
    items = [
        item
        for item in thread.action_items.filter(status=ActionItemStatus.OVERDUE)
        if item.assignee == "client"
    ]
    if not items:
        msg = "There is no overdue client promise to follow up on."
        raise FollowupError(msg)
    draft = followup.draft_client_followup(thread, items, now)
    if draft is None:
        msg = "The follow-up could not be drafted; it was escalated instead."
        raise FollowupError(msg)
    return draft
