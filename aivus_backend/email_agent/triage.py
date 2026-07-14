"""Cheap pre-LLM triage for inbound email (Stage 3, S3-23).

Static header checks run before the expensive classifier so mailing lists,
bounces, autoresponders and our own outbound mail never cost an LLM call. Two
cases are treated apart from plain junk: our own mail (self-detection) and a
client's out-of-office reply, which pauses the thread from code without an LLM.
A per-thread daily cap on LLM calls is the last cost backstop.

This module also owns the pause lifecycle (S3-35), since both entry points into
it live here or next door: the header-detected out-of-office and the classifier's
``pause_until``.
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
    from datetime import datetime

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
    The From address is checked against DMARC first: a bare From match is
    trivially spoofable, and this identity hands the sender the producer's
    powers — freezing the thread into a takeover the agent never leaves, and
    settling the producer's own promises.
    """
    producer = (producer_email or "").strip().lower()
    if not producer or message.from_email.strip().lower() != producer:
        return False
    return safety.is_authenticated_sender(message.headers, producer)


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


def pause_thread(thread: EmailThread, until: datetime) -> None:
    """Pause a thread until ``until``, remembering the state to come back to.

    An existing pause is only ever extended, never shortened: a second
    out-of-office (a header one carries no date and falls back to the default
    window) must not cut short a longer return date the classifier already read
    out of the first one.

    A human takeover outranks a pause and keeps the state: the takeover state is
    what silences the agent, and overwriting it with PAUSED would let the
    drafting guard stop matching and put the agent back on a thread its owner
    took over. The deadline is still recorded, so follow-ups stay off.
    """
    fields = ["paused_until", "updated_at"]
    if thread.state not in (ThreadState.PAUSED, ThreadState.HUMAN_TAKEOVER):
        thread.state_before_pause = thread.state
        thread.state = ThreadState.PAUSED
        fields.extend(["state", "state_before_pause"])
    if thread.paused_until is None or until > thread.paused_until:
        thread.paused_until = until
    thread.save(update_fields=fields)


def resume_thread(thread: EmailThread) -> bool:
    """Lift a pause and restore the state the thread was in before it.

    Called both when the pause window elapses and when a genuine inbound arrives:
    a real message means the client is back, whatever the return date said. The
    pre-gate filters auto-replies before this runs, so an out-of-office extension
    can never cancel its own pause.
    """
    if thread.state != ThreadState.PAUSED:
        return False
    thread.state = thread.state_before_pause or ThreadState.MONITORING
    thread.state_before_pause = ""
    thread.paused_until = None
    thread.save(
        update_fields=["state", "state_before_pause", "paused_until", "updated_at"]
    )
    AgentLog.objects.create(
        thread=thread,
        project=thread.project,
        event="thread_resumed",
        payload={"state": thread.state},
    )
    return True


def apply_ooo_pause(message: EmailMessage) -> None:
    """Pause the thread on a header-detected out-of-office reply, without an LLM."""
    thread = message.thread
    message.intent = MessageIntent.AUTO_REPLY
    message.is_auto_reply = True
    message.processed_at = timezone.now()
    message.save(update_fields=["intent", "is_auto_reply", "processed_at"])

    pause_thread(thread, timezone.now() + DEFAULT_OOO_PAUSE)

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
