"""Cheap pre-LLM triage for inbound email (Stage 3, S3-23).

Static header checks run before the expensive classifier so mailing lists,
bounces, autoresponders and our own outbound mail never cost an LLM call. Two
cases are treated apart from plain junk: our own mail (self-detection) and a
client's out-of-office reply, which pauses the thread from code without an LLM.
A per-thread daily cap on LLM calls is the last cost backstop.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from django.utils import timezone

from aivus_backend.email_agent import notifications
from aivus_backend.email_agent import safety
from aivus_backend.email_agent.events import NotificationEvent
from aivus_backend.email_agent.models import AgentLog
from aivus_backend.email_agent.models import EmailDirection
from aivus_backend.email_agent.models import EmailMessage
from aivus_backend.email_agent.models import MessageIntent
from aivus_backend.email_agent.models import ThreadState

if TYPE_CHECKING:
    from aivus_backend.email_agent.models import EmailThread
    from aivus_backend.users.models import Vendor

THREAD_LLM_DAILY_CAP = 20
DEFAULT_OOO_PAUSE = timedelta(days=3)

CLASSIFIED_EVENTS = ("classified", "classify_failed")

_KNOWN_OUT_WINDOW = timedelta(days=7)
_KNOWN_OUT_CAP = 500

REASON_SELF = "self"
REASON_AUTO_OR_BULK = "auto_or_bulk"
REASON_OOO = "auto_reply_ooo"
REASON_LLM_CAP = "llm_cap"


@dataclass(frozen=True)
class TriageResult:
    proceed: bool
    reason: str


def known_out_message_ids(
    vendor: Vendor, thread: EmailThread | None = None
) -> set[str]:
    query = EmailMessage.objects.filter(direction=EmailDirection.OUT).exclude(
        message_id_header=""
    )
    if thread is not None:
        query = query.filter(thread=thread)
    else:
        since = timezone.now() - _KNOWN_OUT_WINDOW
        query = query.filter(thread__vendor=vendor, created_at__gte=since)
    return set(query.values_list("message_id_header", flat=True)[:_KNOWN_OUT_CAP])


def is_producer_reply(message: EmailMessage, producer_email: str) -> bool:
    """Whether this inbound is the producer replying into the thread.

    Self mail (our own agent sends, marked X-Aivus-Agent) is already filtered by
    the pre-gate, so a match on the producer address means a real human takeover.
    """
    producer = (producer_email or "").strip().lower()
    return bool(producer) and message.from_email.strip().lower() == producer


def is_out_of_office(raw_headers: dict) -> bool:
    headers = safety.normalize_headers(raw_headers)
    if "auto-replied" in headers.get("auto-submitted", "").lower():
        return True
    if headers.get("x-autoreply", "").strip().lower() in {"yes", "true"}:
        return True
    return bool(headers.get("x-autorespond", "").strip())


def llm_cap_reached(thread: EmailThread) -> bool:
    since = timezone.now() - timedelta(hours=24)
    used = AgentLog.objects.filter(
        thread=thread,
        event__in=CLASSIFIED_EVENTS,
        created_at__gte=since,
    ).count()
    return used >= THREAD_LLM_DAILY_CAP


def pre_gate(message: EmailMessage) -> TriageResult:
    vendor = message.thread.vendor
    known = known_out_message_ids(vendor, message.thread)
    if safety.is_self_message(message.headers, str(vendor.id), known):
        return TriageResult(proceed=False, reason=REASON_SELF)
    if is_out_of_office(message.headers):
        return TriageResult(proceed=False, reason=REASON_OOO)
    if safety.is_auto_or_bulk(message.headers):
        return TriageResult(proceed=False, reason=REASON_AUTO_OR_BULK)
    if llm_cap_reached(message.thread):
        return TriageResult(proceed=False, reason=REASON_LLM_CAP)
    return TriageResult(proceed=True, reason="")


def apply_ooo_pause(message: EmailMessage) -> None:
    """Pause the thread on a header-detected out-of-office reply, without an LLM."""
    thread = message.thread
    message.intent = MessageIntent.AUTO_REPLY
    message.is_auto_reply = True
    message.processed_at = timezone.now()
    message.save(update_fields=["intent", "is_auto_reply", "processed_at"])

    thread.state = ThreadState.PAUSED
    thread.paused_until = timezone.now() + DEFAULT_OOO_PAUSE
    thread.save(update_fields=["state", "paused_until", "updated_at"])

    AgentLog.objects.create(
        thread=thread,
        project=thread.project,
        event="ooo_paused",
        payload={"from_email": message.from_email},
    )
    notifications.notify(
        thread.vendor,
        NotificationEvent.OOO_PAUSED,
        {"lines": [f"From: {message.from_email}"]},
        dedup_key=str(thread.id),
    )
