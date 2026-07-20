"""Thread memory and action-item tracking (Stage 3, S3-30/31).

Gives the agent structured memory per thread and turns the classifier's extracted
promises into ActionItem rows: who owes what, by when, and whose ball it is. Memory
updates are targeted (never blank out a known field, preserve the rest) because a
whole-profile overwrite is the classic failure mode. Promises dedupe against the
party's pending items, and a party's own reply auto-clears the items it did not
re-promise — replying is not the same as delivering.
"""

from __future__ import annotations

import re
from datetime import datetime
from difflib import SequenceMatcher
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from django.utils import timezone

from aivus_backend.email_agent import safety
from aivus_backend.email_agent.models import ActionAssignee
from aivus_backend.email_agent.models import ActionItem
from aivus_backend.email_agent.models import ActionItemStatus
from aivus_backend.email_agent.models import AgentLog
from aivus_backend.email_agent.models import VendorAgentProfile

if TYPE_CHECKING:
    from collections.abc import Collection
    from uuid import UUID

    from aivus_backend.email_agent.classification import Classification
    from aivus_backend.email_agent.models import EmailMessage
    from aivus_backend.email_agent.models import EmailThread
    from aivus_backend.users.models import Vendor

_MEMORY_KEYS = ("wants", "deadline", "budget", "missing")
_DUPLICATE_RATIO = 0.85
_VALID_ASSIGNEES = set(ActionAssignee.values)
_WHITESPACE_RE = re.compile(r"\s+")
_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PENDING_STATUSES = (ActionItemStatus.OPEN, ActionItemStatus.OVERDUE)


def _vendor_tzname(vendor: Vendor) -> str:
    profile = VendorAgentProfile.objects.filter(vendor=vendor).first()
    if profile is None:
        return "UTC"
    return profile.working_hours.get("timezone") or "UTC"


def _safe_zone(tzname: str) -> ZoneInfo:
    try:
        return ZoneInfo(tzname)
    except (KeyError, ValueError):
        return ZoneInfo("UTC")


def _parse_due(value: object, tzname: str) -> datetime | None:
    """Parse a due date, attaching the vendor timezone to a naive/date-only value.

    A date with no time means end of that day, not the stroke of midnight: a
    client who promised something "Friday" has all of Friday, so a bare date
    resolves to 23:59 local. Otherwise the deadline passes at 00:00 and the
    reminder fires on the morning of the very day it was due.
    """
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    date_only = _DATE_ONLY_RE.match(text) is not None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if date_only:
        parsed = parsed.replace(hour=23, minute=59, second=59)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=_safe_zone(tzname))
    return parsed


def _normalize(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip().lower()


def _find_pending_duplicate(
    thread: EmailThread, assignee: str, text: str
) -> ActionItem | None:
    """A pending item of the same party whose text is the same promise reworded."""
    normalized = _normalize(text)
    for item in ActionItem.objects.filter(
        thread=thread, assignee=assignee, status__in=_PENDING_STATUSES
    ):
        existing = _normalize(item.text)
        if existing == normalized:
            return item
        if SequenceMatcher(None, existing, normalized).ratio() >= _DUPLICATE_RATIO:
            return item
    return None


def _renew(
    item: ActionItem,
    message: EmailMessage,
    text: str,
    due_at: datetime | None,
) -> None:
    """Fold a re-stated promise into the item it restates.

    A new deadline makes an overdue promise pending again and resets the nagging
    budget: the party renegotiated in good faith, so the follow-up count from the
    old deadline must not carry over and silence the new one.
    """
    item.text = text
    item.source_message = message
    fields = ["text", "source_message", "updated_at"]
    if due_at is not None and due_at != item.due_at:
        item.due_at = due_at
        fields.append("due_at")
        if due_at > timezone.now():
            item.status = ActionItemStatus.OPEN
            item.followup_count = 0
            item.last_followup_at = None
            fields.extend(["status", "followup_count", "last_followup_at"])
    item.save(update_fields=fields)


def update_thread_memory(thread: EmailThread, classification: Classification) -> None:
    """Merge the classification into the thread memory without clobbering it.

    Only fields the model actually filled are written (a blank never overwrites a
    known value), and untouched keys are preserved.
    """
    memory = dict(thread.memory or {})
    extracted = classification.extracted
    for key in _MEMORY_KEYS:
        value = extracted.get(key)
        if value:
            memory[key] = value
    if classification.whos_ball:
        memory["whos_ball"] = classification.whos_ball
    if classification.language:
        memory["language"] = classification.language
    thread.memory = memory
    thread.save(update_fields=["memory", "updated_at"])


def persist_action_items(
    message: EmailMessage, classification: Classification
) -> list[ActionItem]:
    """Create or update ActionItems from a message's extracted promises."""
    thread = message.thread
    tzname = _vendor_tzname(thread.vendor)
    tracked: list[ActionItem] = []
    for raw in classification.action_items:
        assignee = str(raw.get("assignee", "")).strip()
        text = str(raw.get("text", "")).strip()
        if assignee not in _VALID_ASSIGNEES or not text:
            continue
        raw_due = raw.get("due_at")
        due_at = _parse_due(raw_due, tzname)
        if due_at is None and isinstance(raw_due, str) and raw_due.strip():
            # A stated-but-unparseable deadline would silently become a never-chased
            # promise; surface it so a non-ISO regression in the model is visible.
            AgentLog.objects.create(
                thread=thread,
                project=thread.project,
                event="due_unparsed",
                payload={"assignee": assignee, "due_at": raw_due[:64]},
            )

        existing = _find_pending_duplicate(thread, assignee, text)
        if existing is not None:
            _renew(existing, message, text, due_at)
            tracked.append(existing)
            continue

        item = ActionItem.objects.create(
            thread=thread,
            project=thread.project,
            assignee=assignee,
            text=text,
            due_at=due_at,
            source_message=message,
        )
        AgentLog.objects.create(
            thread=thread,
            project=thread.project,
            event="promise_tracked",
            payload={"assignee": assignee, "text": text},
        )
        tracked.append(item)
    return tracked


def sender_party(message: EmailMessage) -> str:
    """Which side of the thread sent this message: the producer, or the client.

    The producer identity decides whose promises a message may settle, so a bare
    From match is not enough — an unauthenticated sender claiming the producer's
    address is treated as the client, whose promises are not the producer's.
    """
    profile = VendorAgentProfile.objects.filter(vendor=message.thread.vendor).first()
    producer_email = profile.producer_email if profile is not None else ""
    sender = message.from_email.strip().lower()
    if (
        producer_email
        and sender == producer_email.strip().lower()
        and safety.is_authenticated_sender(message.headers, producer_email)
    ):
        return ActionAssignee.PRODUCER
    return ActionAssignee.CLIENT


def close_fulfilled_items(
    message: EmailMessage,
    classification: Classification,
    *,
    exclude_ids: Collection[UUID] = (),
) -> int:
    """Close only the promises this message actually settles.

    Fulfilment is never inferred from the fact that someone wrote back. A reply
    is not a delivery: a client can answer a question, apologise, or promise
    again while still owing the footage, and closing on activity alone would drop
    exactly the promises this engine exists to chase — silently and for good,
    since nothing ever reopens a closed item. So the classifier names what this
    email delivered and we close that, scoped to the sender's own promises (an
    email can never settle the other side's debts) and minus anything the same
    message merely re-stated (``exclude_ids``, straight from
    ``persist_action_items``).
    """
    fulfilled = [
        item_id
        for item_id in classification.fulfilled_ids
        if item_id not in {str(excluded) for excluded in exclude_ids}
    ]
    if not fulfilled:
        return 0
    return ActionItem.objects.filter(
        thread=message.thread,
        assignee=sender_party(message),
        status__in=_PENDING_STATUSES,
        id__in=fulfilled,
    ).update(status=ActionItemStatus.DONE, updated_at=timezone.now())


def mark_overdue_items(now: datetime) -> int:
    """Flag open items whose deadline has passed as overdue."""
    return ActionItem.objects.filter(
        status=ActionItemStatus.OPEN,
        due_at__isnull=False,
        due_at__lte=now,
    ).update(status=ActionItemStatus.OVERDUE, updated_at=now)
