"""LLM intent classification for inbound email (Stage 3, S3-24/24a).

The classifier turns a normalized email plus the vendor's instructions into a
structured decision. Two safety properties are enforced in code, not the prompt:
the email body is passed as nonce-wrapped untrusted data, and the model's raw
output is coerced into a strict typed result — an invalid intent or a low
confidence can never be trusted into an automatic client reply.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from datetime import UTC
from datetime import datetime
from typing import TYPE_CHECKING
from typing import Any
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from aivus_backend.core.enums import BriefPromptSlug
from aivus_backend.core.llm import call_llm_json
from aivus_backend.email_agent import attachments
from aivus_backend.email_agent import prompts
from aivus_backend.email_agent import safety
from aivus_backend.email_agent import triage
from aivus_backend.email_agent.leads import create_email_lead
from aivus_backend.email_agent.models import ActionItem
from aivus_backend.email_agent.models import ActionItemStatus
from aivus_backend.email_agent.models import AgentLog
from aivus_backend.email_agent.models import EmailThread
from aivus_backend.email_agent.models import MessageIntent
from aivus_backend.email_agent.models import ThreadState
from aivus_backend.email_agent.models import VendorAgentProfile
from aivus_backend.projects.models import Brief

if TYPE_CHECKING:
    from aivus_backend.email_agent.models import EmailMessage
    from aivus_backend.users.models import Vendor

CLASSIFY_TEMPERATURE = 0.2
CLASSIFY_MAX_TOKENS = 2048
CONFIDENCE_THRESHOLD = 0.6

_MAX_PROMISES_SHOWN = 20

DECISION_DRAFT = "draft"
DECISION_ESCALATE = "escalate"
DECISION_SILENT = "silent"

_VALID_INTENTS = set(MessageIntent.values)
_SILENT_INTENTS = {MessageIntent.JUNK, MessageIntent.AUTO_REPLY}


@dataclass(frozen=True)
class Classification:
    """One inbound email's structured decision.

    ``fulfilled_ids`` carries the promises this email settles. The model names
    them by the short keys it was shown; ``classify_message`` resolves those to
    real ActionItem ids before returning, so every consumer sees ids it can trust
    (an id the model invented resolves to nothing and is dropped).
    """

    intent: str
    extracted: dict
    action_items: list[dict]
    fulfilled_ids: list[str]
    whos_ball: str
    safe_to_send: bool
    escalate_reason: str
    pause_until: datetime | None
    confidence: float
    language: str
    urgent: bool
    reasoning: str
    raw: dict


def _profile_for(vendor: Vendor) -> VendorAgentProfile | None:
    return VendorAgentProfile.objects.filter(vendor=vendor).first()


def _clamp_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, confidence))


def _parse_pause_until(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _normalize_language(value: Any) -> str:
    code = str(value or "").strip().lower()[:2]
    return code or "en"


def coerce_classification(raw: Any) -> Classification:
    """Turn raw LLM output into a strict typed result, failing safe on garbage."""
    if not isinstance(raw, dict):
        raw = {}
    intent = str(raw.get("intent", "")).strip()
    escalate_reason = str(raw.get("escalate_reason", "")).strip()
    confidence = _clamp_confidence(raw.get("confidence"))
    if intent not in _VALID_INTENTS:
        intent = MessageIntent.JUNK
        confidence = 0.0
        escalate_reason = escalate_reason or "invalid_intent"

    extracted = raw.get("extracted")
    action_items = raw.get("action_items")
    fulfilled = raw.get("fulfilled")
    return Classification(
        intent=intent,
        extracted=extracted if isinstance(extracted, dict) else {},
        action_items=[item for item in (action_items or []) if isinstance(item, dict)],
        fulfilled_ids=[
            str(key).strip()
            for key in (fulfilled or [])
            if isinstance(key, (str, int)) and str(key).strip()
        ],
        whos_ball=str(raw.get("whos_ball", "")).strip(),
        safe_to_send=bool(raw.get("safe_to_send", False)),
        escalate_reason=escalate_reason,
        pause_until=_parse_pause_until(raw.get("pause_until")),
        confidence=confidence,
        language=_normalize_language(raw.get("language")),
        urgent=bool(raw.get("urgent", False)),
        reasoning=str(raw.get("reasoning", "")).strip(),
        raw=raw,
    )


def open_promise_index(thread: EmailThread) -> dict[str, ActionItem]:
    """The thread's pending promises, keyed by the short id shown to the model.

    Short keys instead of UUIDs: they cost fewer tokens, and a hallucinated one
    simply misses the index rather than resolving to some other vendor's row.
    """
    items = ActionItem.objects.filter(
        thread=thread, status__in=(ActionItemStatus.OPEN, ActionItemStatus.OVERDUE)
    ).order_by("created_at")[:_MAX_PROMISES_SHOWN]
    return {str(number): item for number, item in enumerate(items, start=1)}


def _promises_block(index: dict[str, ActionItem]) -> str:
    if not index:
        return ""
    listing = "\n".join(
        f"[{key}] {item.assignee} promised: {item.text}" for key, item in index.items()
    )
    _nonce, wrapped = safety.wrap_untrusted(listing)
    return f"\n\n<open_promises>\n{wrapped}\n</open_promises>"


def _today_line(vendor: Vendor) -> str:
    """Today, in the vendor's timezone: the model cannot resolve "next Friday" blind."""
    profile = _profile_for(vendor)
    tzname = (profile.working_hours.get("timezone") if profile else None) or "UTC"
    try:
        zone = ZoneInfo(tzname)
    except (KeyError, ValueError):
        zone = ZoneInfo("UTC")
    return f"Today is {timezone.now().astimezone(zone):%A, %Y-%m-%d} ({tzname})."


def classify_message(message: EmailMessage) -> tuple[Classification, dict]:
    """Classify one inbound message. Raises on an unrecoverable LLM failure."""
    thread = message.thread
    profile = _profile_for(thread.vendor)
    instructions = prompts.compile_vendor_instructions(profile)
    body = prompts.load_prompt_body(BriefPromptSlug.EMAIL_CLASSIFICATION)
    system_prompt = prompts.fill_instructions(body, instructions)

    index = open_promise_index(thread)
    _nonce, wrapped_body = safety.wrap_untrusted(message.body_clean)
    user_block = (
        f"{_today_line(thread.vendor)}\n"
        f"From: {message.from_email}\nSubject: {message.subject}\n\n"
        f"{wrapped_body}{_promises_block(index)}"
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_block},
    ]
    raw, response = call_llm_json(
        model=prompts.model_for_prompt(BriefPromptSlug.EMAIL_CLASSIFICATION),
        messages=messages,
        temperature=CLASSIFY_TEMPERATURE,
        max_tokens=CLASSIFY_MAX_TOKENS,
    )
    result = coerce_classification(raw)
    resolved = [str(index[key].id) for key in result.fulfilled_ids if key in index]
    return replace(result, fulfilled_ids=resolved), prompts.trace_entry(
        "classify", response
    )


def apply_classification(
    message: EmailMessage,
    classification: Classification,
    trace: dict,
) -> None:
    """Persist the classification onto the message, thread and agent log."""
    thread = message.thread
    message.intent = classification.intent
    message.is_auto_reply = classification.intent == MessageIntent.AUTO_REPLY
    message.save(update_fields=["intent", "is_auto_reply"])

    if classification.pause_until is not None:
        triage.pause_thread(thread, classification.pause_until)

    AgentLog.objects.create(
        thread=thread,
        project=thread.project,
        event="classified",
        payload={
            "intent": classification.intent,
            "confidence": classification.confidence,
            "language": classification.language,
            "urgent": classification.urgent,
            "whos_ball": classification.whos_ball,
            "trace": trace,
        },
    )


_LEAD_INTENTS = frozenset({MessageIntent.ORDER, MessageIntent.FOLLOW_UP})


def _signals_project(classification: Classification) -> bool:
    """True when the classification body describes a project the vendor could work on.

    The client can commit to a project inside a follow_up thread (a chase becomes
    an order the moment they say what they want made), so intent alone is not
    enough. ``wants`` is filled by the model with a one-line project description
    exactly when there is one, so it is the canonical signal for lead creation.
    """
    wants = (classification.extracted or {}).get("wants") or ""
    return bool(str(wants).strip())


def wire_lead(message: EmailMessage, classification: Classification) -> Brief | None:
    """Create a canonical lead once the thread first signals a project.

    Anything that names a project — a fresh order, or a follow_up where the
    client finally states what they want made — becomes a lead. The thread row
    is locked and re-checked so a crashed retry or a second signal on the same
    thread cannot spawn a second lead. Pure questions/edits/OOO never create
    one; they ride the already-stitched thread.
    """
    if classification.intent not in _LEAD_INTENTS:
        return None
    if classification.intent != MessageIntent.ORDER and not _signals_project(
        classification
    ):
        return None

    with transaction.atomic():
        thread = EmailThread.objects.select_for_update().get(id=message.thread_id)
        if thread.project_id is not None:
            return None
        brief, project = create_email_lead(
            vendor=thread.vendor,
            message=f"Subject: {message.subject}\n\n{message.body_clean}",
            contact_email=message.from_email,
            contact_name=thread.client_name,
            thread=thread,
        )
        if classification.language:
            Brief.objects.filter(id=brief.id).update(
                document_language=classification.language
            )
            brief.document_language = classification.language
        attachments.link_thread_attachments(thread.id, brief)
        AgentLog.objects.create(
            thread=thread,
            project=project,
            event="lead_created",
            payload={"brief_id": str(brief.id)},
        )
    return brief


def reply_decision(message: EmailMessage, classification: Classification) -> str:
    """Structural fail-safe deciding what happens after classification.

    Junk and autoresponders stay silent; anything the model was not confident
    about, or explicitly flagged unsafe, escalates to the producer instead of
    drafting a client reply. Only a confident, safe, actionable email drafts.
    """
    if message.thread.state == ThreadState.HUMAN_TAKEOVER:
        return DECISION_SILENT
    # A client that asks to defer the conversation ("come back next week", an
    # OOO auto-reply, "напишите через неделю") sets pause_until; keep quiet
    # regardless of intent — a helpful draft here reads as ignoring the ask.
    # Checked on ``pause_until`` rather than ``thread.state == PAUSED`` because
    # ``pause_thread`` refuses to overwrite HUMAN_TAKEOVER, so the state can
    # stay unchanged while the request was legitimate.
    if classification.pause_until is not None:
        return DECISION_SILENT
    if classification.intent in _SILENT_INTENTS:
        return DECISION_SILENT
    if classification.confidence < CONFIDENCE_THRESHOLD:
        return DECISION_ESCALATE
    if not classification.safe_to_send:
        return DECISION_ESCALATE
    if classification.escalate_reason:
        return DECISION_ESCALATE
    return DECISION_DRAFT
