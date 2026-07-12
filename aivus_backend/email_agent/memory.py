"""Thread memory and action-item tracking (Stage 3, S3-30/31).

Gives the agent structured memory per thread and turns the classifier's extracted
promises into ActionItem rows: who owes what, by when, and whose ball it is. Memory
updates are targeted (never blank out a known field, preserve the rest) because a
whole-profile overwrite is the classic failure mode. Promises dedupe against open
items of the same party, and a party's own reply auto-clears its open items.
"""

from __future__ import annotations

import re
from datetime import datetime
from difflib import SequenceMatcher
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from django.utils import timezone

from aivus_backend.email_agent.models import ActionAssignee
from aivus_backend.email_agent.models import ActionItem
from aivus_backend.email_agent.models import ActionItemStatus
from aivus_backend.email_agent.models import AgentLog
from aivus_backend.email_agent.models import VendorAgentProfile

if TYPE_CHECKING:
    from aivus_backend.email_agent.classification import Classification
    from aivus_backend.email_agent.models import EmailMessage
    from aivus_backend.email_agent.models import EmailThread
    from aivus_backend.users.models import Vendor

_MEMORY_KEYS = ("wants", "deadline", "budget", "missing")
_DUPLICATE_RATIO = 0.85
_VALID_ASSIGNEES = set(ActionAssignee.values)
_WHITESPACE_RE = re.compile(r"\s+")


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
    """Parse a due date, attaching the vendor timezone to a naive/date-only value."""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=_safe_zone(tzname))
    return parsed


def _normalize(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip().lower()


def _find_open_duplicate(
    thread: EmailThread, assignee: str, text: str
) -> ActionItem | None:
    """An open item of the same party whose text is the same promise reworded."""
    normalized = _normalize(text)
    for item in ActionItem.objects.filter(
        thread=thread, assignee=assignee, status=ActionItemStatus.OPEN
    ):
        existing = _normalize(item.text)
        if existing == normalized:
            return item
        if SequenceMatcher(None, existing, normalized).ratio() >= _DUPLICATE_RATIO:
            return item
    return None


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
        due_at = _parse_due(raw.get("due_at"), tzname)

        existing = _find_open_duplicate(thread, assignee, text)
        if existing is not None:
            existing.text = text
            existing.source_message = message
            if due_at is not None:
                existing.due_at = due_at
            existing.save(
                update_fields=["text", "source_message", "due_at", "updated_at"]
            )
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


def close_fulfilled_items(message: EmailMessage) -> int:
    """Auto-clear the sender party's open items: activity signals the ball moved.

    A reply from the client (or producer) clears that party's pending items so we
    stop waiting on them. The agent's own items are never cleared this way.
    """
    thread = message.thread
    profile = VendorAgentProfile.objects.filter(vendor=thread.vendor).first()
    producer_email = profile.producer_email if profile is not None else ""
    sender = message.from_email.strip().lower()
    if producer_email and sender == producer_email.strip().lower():
        assignee = ActionAssignee.PRODUCER
    else:
        assignee = ActionAssignee.CLIENT
    return ActionItem.objects.filter(
        thread=thread, assignee=assignee, status=ActionItemStatus.OPEN
    ).update(status=ActionItemStatus.DONE, updated_at=timezone.now())


def mark_overdue_items(now: datetime) -> int:
    """Flag open items whose deadline has passed as overdue."""
    return ActionItem.objects.filter(
        status=ActionItemStatus.OPEN,
        due_at__isnull=False,
        due_at__lte=now,
    ).update(status=ActionItemStatus.OVERDUE, updated_at=now)
