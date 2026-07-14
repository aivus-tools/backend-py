"""Deadline timers and the follow-up engine (Stage 3, S3-33/34/35).

Every tracked promise carries a deadline, and this is what happens when one
passes. A client who owes something gets a soft reminder in the vendor's tone; a
producer who owes something gets a ping in their notification channel. Both are
swept from a single beat entry rather than a per-promise countdown, so nothing is
lost when a worker restarts.

Three properties keep the engine from turning into a nag:

- aggregation: one draft (or one ping) per thread covers every promise that came
  due there, never one message per promise;
- limits: a promise is chased a fixed number of times, with a gap between tries,
  and a vendor has a daily ceiling on follow-up drafts;
- silence where silence is due: a paused thread (client out of office) and a
  thread a human took over never produce a client-facing follow-up, and neither
  does one that already has a draft waiting for review.

The client reminder is a draft like any other — the MVP never sends to a client
without a human approving it (S3-20/21).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError
from django.db import transaction
from django.db.models import F
from django.db.models import Q
from django.utils import timezone

from aivus_backend.core.enums import BriefPromptSlug
from aivus_backend.core.llm import call_llm_json
from aivus_backend.email_agent import memory
from aivus_backend.email_agent import notifications
from aivus_backend.email_agent import prompts
from aivus_backend.email_agent import reply
from aivus_backend.email_agent import safety
from aivus_backend.email_agent import triage
from aivus_backend.email_agent.events import NotificationEvent
from aivus_backend.email_agent.models import ActionAssignee
from aivus_backend.email_agent.models import ActionItem
from aivus_backend.email_agent.models import ActionItemStatus
from aivus_backend.email_agent.models import AgentLog
from aivus_backend.email_agent.models import EmailDirection
from aivus_backend.email_agent.models import EmailThread
from aivus_backend.email_agent.models import OutboundDraft
from aivus_backend.email_agent.models import OutboundDraftKind
from aivus_backend.email_agent.models import OutboundDraftStatus
from aivus_backend.email_agent.models import ThreadState
from aivus_backend.email_agent.models import VendorAgentProfile

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from aivus_backend.email_agent.models import EmailMessage
    from aivus_backend.users.models import Vendor

FOLLOWUP_TEMPERATURE = 0.4
FOLLOWUP_MAX_TOKENS = 600

CLIENT_FOLLOWUP_MAX = 2
CLIENT_FOLLOWUP_GAP = timedelta(hours=24)
PRODUCER_PING_MAX = 3
PRODUCER_NEAR_WINDOW = timedelta(hours=24)
VENDOR_DAILY_FOLLOWUP_CAP = 20
SWEEP_BATCH = 200
SWEEP_LOCK_TTL_SECONDS = 3600

STAGE_NEAR = "near"
STAGE_OVERDUE = "overdue"

_SWEEP_LOCK_KEY = "email_followup_sweep"
_SILENT_STATES = (ThreadState.PAUSED, ThreadState.HUMAN_TAKEOVER)


def _frontend_url() -> str:
    return getattr(settings, "FRONTEND_URL", "https://go.aivus.co").rstrip("/")


def _dashboard_link() -> str:
    return f"{_frontend_url()}/app/email-agent"


def _profile_for(vendor: Vendor) -> VendorAgentProfile | None:
    return VendorAgentProfile.objects.filter(vendor=vendor).first()


def _last_inbound(thread: EmailThread) -> EmailMessage | None:
    return (
        thread.messages.filter(direction=EmailDirection.IN)
        .order_by("-created_at")
        .first()
    )


def _group_by_thread(items: list[ActionItem]) -> list[list[ActionItem]]:
    """Bucket due promises by thread so one thread yields one reminder."""
    grouped: dict[UUID, list[ActionItem]] = defaultdict(list)
    for item in items:
        grouped[item.thread_id].append(item)
    return list(grouped.values())


def _touch(items: list[ActionItem], now: datetime) -> None:
    ActionItem.objects.filter(id__in=[item.id for item in items]).update(
        followup_count=F("followup_count") + 1,
        last_followup_at=now,
        updated_at=now,
    )


def _claim(items: list[ActionItem], now: datetime) -> list[ActionItem]:
    """Atomically spend one chase attempt per item, returning the ones claimed.

    The claim is a conditional update guarded on the exact counter and timestamp
    that were read, so two overlapping sweeps cannot both spend the same item: the
    loser's WHERE clause matches nothing. This makes the counter correct on its
    own, independent of the sweep lock — the lock only saves duplicate LLM work.
    Spending up front (not after a successful draft) is deliberate: a prompt that
    keeps failing on a thread must cost one attempt per gap and then escalate, not
    retry every sweep forever.
    """
    claimed: list[ActionItem] = []
    for item in items:
        updated = ActionItem.objects.filter(
            id=item.id,
            followup_count=item.followup_count,
            last_followup_at=item.last_followup_at,
        ).update(
            followup_count=item.followup_count + 1,
            last_followup_at=now,
            updated_at=now,
        )
        if updated:
            claimed.append(item)
    return claimed


def resume_paused_threads(now: datetime) -> int:
    """Lift pauses whose return date has arrived so follow-ups can fire again."""
    threads = list(
        EmailThread.objects.filter(
            state=ThreadState.PAUSED,
            paused_until__isnull=False,
            paused_until__lte=now,
            deleted_at__isnull=True,
        )[:SWEEP_BATCH]
    )
    return sum(1 for thread in threads if triage.resume_thread(thread))


def due_client_items(now: datetime) -> list[ActionItem]:
    """Overdue client promises still worth chasing, oldest deadline first.

    Silenced threads are excluded in the query, not after it: a vendor with a
    batch's worth of paused promises would otherwise fill the sweep with items it
    is going to skip anyway, and starve every thread behind them forever.
    """
    return list(
        ActionItem.objects.filter(
            assignee=ActionAssignee.CLIENT,
            status=ActionItemStatus.OVERDUE,
            followup_count__lt=CLIENT_FOLLOWUP_MAX,
            thread__deleted_at__isnull=True,
        )
        .exclude(thread__state__in=_SILENT_STATES)
        .exclude(thread__drafts__status=OutboundDraftStatus.PENDING)
        .filter(
            Q(last_followup_at__isnull=True)
            | Q(last_followup_at__lte=now - CLIENT_FOLLOWUP_GAP)
        )
        .select_related("thread", "thread__vendor")
        .order_by("due_at")[:SWEEP_BATCH]
    )


def due_producer_items(now: datetime) -> list[ActionItem]:
    """Producer promises already late or coming due inside the near window."""
    return list(
        ActionItem.objects.filter(
            assignee=ActionAssignee.PRODUCER,
            status__in=(ActionItemStatus.OPEN, ActionItemStatus.OVERDUE),
            due_at__isnull=False,
            due_at__lte=now + PRODUCER_NEAR_WINDOW,
            followup_count__lt=PRODUCER_PING_MAX,
            thread__deleted_at__isnull=True,
        )
        .select_related("thread", "thread__vendor")
        .order_by("due_at")[:SWEEP_BATCH]
    )


def accepts_client_followup(thread: EmailThread, now: datetime) -> bool:
    """Whether a client-facing reminder may be prepared for this thread now.

    A paused or human-driven thread stays quiet, a thread that already has a draft
    waiting does not get a second one piled on the producer's queue, and nothing
    is written outside the vendor's working hours — the next sweep inside the
    window picks it up unchanged.
    """
    if thread.state in _SILENT_STATES:
        return False
    profile = _profile_for(thread.vendor)
    working_hours = profile.working_hours if profile is not None else {}
    if not notifications.is_within_working_hours(working_hours, now):
        return False
    return not OutboundDraft.objects.filter(
        thread=thread,
        status=OutboundDraftStatus.PENDING,
    ).exists()


def _followup_budget_left(vendor: Vendor, now: datetime) -> int:
    used = OutboundDraft.objects.filter(
        thread__vendor=vendor,
        kind=OutboundDraftKind.FOLLOW_UP,
        created_at__gte=now - timedelta(hours=24),
    ).count()
    return VENDOR_DAILY_FOLLOWUP_CAP - used


def _build_user_block(thread: EmailThread, items: list[ActionItem]) -> str:
    language = str((thread.memory or {}).get("language") or "en")
    promises = "\n".join(f"- {item.text}" for item in items)
    _nonce, wrapped = safety.wrap_untrusted(
        f"Subject: {thread.canonical_subject}\n"
        f"Promises the client made and has not kept:\n{promises}"
    )
    return (
        f"Client language: {language}\n\n"
        f"Write the follow-up slots for this thread:\n{wrapped}"
    )


def propose_followup(
    thread: EmailThread, items: list[ActionItem]
) -> tuple[str | None, dict]:
    """Draft a soft reminder body, or None when it cannot be written safely.

    The promise texts are model-extracted from an untrusted email, so they go back
    to the model wrapped as data, and the assembled letter runs the same
    commitment blacklist and URL sanitizer as a first reply.
    """
    profile = _profile_for(thread.vendor)
    instructions = prompts.compile_vendor_instructions(profile)
    body = prompts.load_prompt_body(BriefPromptSlug.EMAIL_FOLLOWUP)
    system_prompt = prompts.fill_instructions(body, instructions)

    raw, response = call_llm_json(
        model=prompts.model_for_prompt(BriefPromptSlug.EMAIL_FOLLOWUP),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": _build_user_block(thread, items)},
        ],
        temperature=FOLLOWUP_TEMPERATURE,
        max_tokens=FOLLOWUP_MAX_TOKENS,
    )
    trace = prompts.trace_entry("followup", response)

    slots = raw.get("slots") if isinstance(raw.get("slots"), dict) else raw
    if not isinstance(slots, dict):
        return None, trace
    rendered = reply.render_skeleton(slots)
    if not rendered.strip() or reply.has_forbidden_commitments(rendered):
        return None, trace
    return safety.sanitize_outbound(rendered), trace


def create_followup_draft(
    thread: EmailThread, items: list[ActionItem], body: str
) -> OutboundDraft | None:
    """Queue the reminder as a pending draft threaded onto the last client mail."""
    try:
        with transaction.atomic():
            draft = OutboundDraft.objects.create(
                thread=thread,
                in_reply_to_message=_last_inbound(thread),
                kind=OutboundDraftKind.FOLLOW_UP,
                body=body,
                status=OutboundDraftStatus.PENDING,
                expires_at=timezone.now() + reply.DRAFT_TTL,
                metadata={
                    "action": "remind_client_promise",
                    "language": str((thread.memory or {}).get("language") or "en"),
                    "action_item_ids": [str(item.id) for item in items],
                },
            )
    except IntegrityError:
        return None
    return draft


def _escalate_followup(thread: EmailThread, event: str, detail: str) -> None:
    AgentLog.objects.create(
        thread=thread,
        project=thread.project,
        event=event,
        payload={"detail": detail},
    )
    notifications.notify(
        thread.vendor,
        NotificationEvent.ESCALATION,
        {
            "lines": [
                f"Subject: {thread.canonical_subject or '(no subject)'}",
                "A client promise is overdue and the reminder could not be written.",
            ],
            "cta_url": _dashboard_link(),
        },
        dedup_key=f"{event}:{thread.id}",
    )


def draft_client_followup(
    thread: EmailThread, items: list[ActionItem], now: datetime
) -> OutboundDraft | None:
    """Write and queue one reminder covering every overdue promise on the thread."""
    items = _claim(items, now)
    if not items:
        return None
    try:
        body, _trace = propose_followup(thread, items)
    except (ValueError, RuntimeError) as exc:
        _escalate_followup(thread, "followup_failed", str(exc))
        return None
    if body is None:
        _escalate_followup(thread, "followup_blocked", "unsafe or empty draft")
        return None

    draft = create_followup_draft(thread, items, body)
    if draft is None:
        return None

    AgentLog.objects.create(
        thread=thread,
        project=thread.project,
        event="followup_drafted",
        payload={"promises": len(items)},
    )
    notifications.notify(
        thread.vendor,
        NotificationEvent.DRAFT_CREATED,
        {
            "lines": [f"Subject: {thread.canonical_subject or '(no subject)'}"],
            "cta_url": _dashboard_link(),
        },
        dedup_key=str(draft.id),
    )
    return draft


def sweep_client_followups(now: datetime) -> int:
    """Prepare a soft reminder for every thread with an overdue client promise."""
    budget: dict[UUID, int] = {}
    drafted = 0
    for group in _group_by_thread(due_client_items(now)):
        thread = group[0].thread
        if not accepts_client_followup(thread, now):
            continue
        vendor_id = thread.vendor_id
        if vendor_id not in budget:
            budget[vendor_id] = _followup_budget_left(thread.vendor, now)
        if budget[vendor_id] <= 0:
            continue
        if draft_client_followup(thread, group, now) is None:
            continue
        budget[vendor_id] -= 1
        drafted += 1
    return drafted


def _ping_lines(thread: EmailThread, items: list[ActionItem]) -> list[str]:
    lines = [f"Subject: {thread.canonical_subject or '(no subject)'}"]
    for item in items:
        due = f" (due {item.due_at:%Y-%m-%d %H:%M})" if item.due_at else ""
        lines.append(f"- {item.text}{due}")
    return lines


def ping_producer(thread: EmailThread, items: list[ActionItem], now: datetime) -> bool:
    """Tell the producer about their own promises coming due on one thread.

    Near-due and overdue are separate stages so a promise that slips still raises
    a second, louder ping, while repeats inside a stage collapse into the notify
    dedup window. A ping the dispatcher drops does not spend the chase budget.
    """
    stage = (
        STAGE_OVERDUE
        if any(item.status == ActionItemStatus.OVERDUE for item in items)
        else STAGE_NEAR
    )
    log = notifications.notify(
        thread.vendor,
        NotificationEvent.PROMISE_DUE,
        {"lines": _ping_lines(thread, items), "cta_url": _dashboard_link()},
        dedup_key=f"{thread.id}:{stage}",
    )
    if log is None:
        return False
    _touch(items, now)
    AgentLog.objects.create(
        thread=thread,
        project=thread.project,
        event="promise_due_ping",
        payload={"stage": stage, "promises": len(items)},
    )
    return True


def sweep_producer_pings(now: datetime) -> int:
    """Ping the producer once per thread about promises due or already late.

    A paused or human-driven thread still counts: the pause means the client is
    away, not that the producer stopped owing the deliverable, and this ping goes
    to the producer's own channel, never to the client.
    """
    return sum(
        1
        for group in _group_by_thread(due_producer_items(now))
        if ping_producer(group[0].thread, group, now)
    )


def run_sweep(now: datetime) -> int:
    """One pass of the deadline engine, in dependency order.

    Deadlines are flagged first so the same pass acts on what just came due, and
    pauses are lifted before the client sweep so a thread whose return date has
    arrived is chased immediately rather than a beat later.

    A lock keeps two beats from doing the same model work at once, but it is only
    an optimisation: correctness rests on the atomic per-item claim in ``_claim``,
    not on the lock holding for the whole pass. The token guard means a pass whose
    lock expired mid-run never deletes a successor's lock. A skipped pass costs
    nothing — the next beat sees the same due items.
    """
    token = uuid4().hex
    if not cache.add(_SWEEP_LOCK_KEY, token, SWEEP_LOCK_TTL_SECONDS):
        return 0
    try:
        memory.mark_overdue_items(now)
        resume_paused_threads(now)
        return sweep_producer_pings(now) + sweep_client_followups(now)
    finally:
        if cache.get(_SWEEP_LOCK_KEY) == token:
            cache.delete(_SWEEP_LOCK_KEY)
