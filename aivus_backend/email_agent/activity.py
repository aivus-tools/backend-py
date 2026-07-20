"""Human-readable thread activity timeline (Stage 3, S3-32).

Renders the per-thread AgentLog plus the messages into a plain-language history a
vendor can read in the cabinet: received, replied, promised, escalated, paused.
Feeds the mini-CRM feed (S3-38) and analytics (S3-42).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aivus_backend.email_agent.api.serializers import serialize_action_item
from aivus_backend.email_agent.models import EmailDirection

if TYPE_CHECKING:
    from collections.abc import Callable

    from aivus_backend.email_agent.models import AgentLog
    from aivus_backend.email_agent.models import EmailMessage
    from aivus_backend.email_agent.models import EmailThread

_LABELS: dict[str, Callable[[dict], str]] = {
    "classified": lambda p: f"Classified as {p.get('intent', 'unknown')}",
    "ignored": lambda p: f"Ignored ({p.get('reason', 'noise')})",
    "lead_created": lambda p: "Lead created",
    "draft_created": lambda p: f"Draft prepared (variant {p.get('variant', '?')})",
    "draft_sent": lambda p: "Reply sent" + (" (edited)" if p.get("edited") else ""),
    "draft_rejected": lambda p: "Draft rejected",
    "escalated": lambda p: f"Escalated ({p.get('reason') or 'low confidence'})",
    "classify_failed": lambda p: "Classification failed; escalated",
    "reply_blocked": lambda p: "Reply blocked; escalated",
    "human_takeover": lambda p: "Producer took over the thread",
    "ooo_paused": lambda p: "Client is out of office; follow-ups paused",
    "thread_resumed": lambda p: "Out-of-office over; the thread is active again",
    "promise_tracked": (
        lambda p: f"{str(p.get('assignee', 'someone')).capitalize()} promised: "
        f"{p.get('text', '')}"
    ),
    "followup_drafted": lambda p: "Follow-up reminder prepared for the client",
    "followup_blocked": lambda p: "Follow-up could not be written; escalated",
    "followup_failed": lambda p: "Follow-up generation failed; escalated",
    "promise_due_ping": (
        lambda p: "Producer reminded about overdue promises"
        if p.get("stage") == "overdue"
        else "Producer reminded about an approaching deadline"
    ),
}


def log_event(
    thread: EmailThread,
    event: str,
    *,
    payload: dict | None = None,
    project=None,
) -> AgentLog:
    """Record a thread event for the activity timeline."""
    from aivus_backend.email_agent.models import AgentLog  # noqa: PLC0415

    return AgentLog.objects.create(
        thread=thread,
        project=project if project is not None else thread.project,
        event=event,
        payload=payload or {},
    )


def render_log_entry(log: AgentLog) -> str:
    renderer = _LABELS.get(log.event)
    if renderer is not None:
        return renderer(log.payload or {})
    return log.event.replace("_", " ").capitalize()


_MESSAGE_PREVIEW_MAX = 300


def _message_event(message: EmailMessage) -> dict:
    label = "Received email" if message.direction == EmailDirection.IN else "Reply sent"
    preview_source = (message.body_clean or "").strip()
    return {
        "kind": "message",
        "text": f"{label}: {message.subject or '(no subject)'}",
        "direction": message.direction,
        "from": message.from_email,
        "subject": message.subject,
        "preview": preview_source[:_MESSAGE_PREVIEW_MAX],
        "createdAt": message.created_at.isoformat(),
    }


def _log_event(log: AgentLog) -> dict:
    return {
        "kind": "log",
        "event": log.event,
        "text": render_log_entry(log),
        "createdAt": log.created_at.isoformat(),
    }


def serialize_activity(thread: EmailThread) -> dict:
    """Merged, time-ordered timeline of messages and agent events for a thread."""
    events: list[dict] = [_message_event(m) for m in thread.messages.all()]
    events.extend(_log_event(log) for log in thread.logs.all())
    events.sort(key=lambda entry: entry["createdAt"])

    action_items = [
        serialize_action_item(item)
        for item in thread.action_items.order_by("-created_at")
    ]
    return {
        "threadId": str(thread.id),
        "clientEmail": thread.client_email,
        "subject": thread.canonical_subject,
        "state": thread.state,
        "memory": thread.memory or {},
        "actionItems": action_items,
        "events": events,
    }
