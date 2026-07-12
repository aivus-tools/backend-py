"""LLM intent classification for inbound email (Stage 3, S3-24/24a).

The classifier turns a normalized email plus the vendor's instructions into a
structured decision. Two safety properties are enforced in code, not the prompt:
the email body is passed as nonce-wrapped untrusted data, and the model's raw
output is coerced into a strict typed result — an invalid intent or a low
confidence can never be trusted into an automatic client reply.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from typing import TYPE_CHECKING
from typing import Any

from django.db import transaction

from aivus_backend.core.enums import BriefPromptSlug
from aivus_backend.core.llm import call_llm_json
from aivus_backend.email_agent import prompts
from aivus_backend.email_agent import safety
from aivus_backend.email_agent.leads import create_email_lead
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
CLASSIFY_MAX_TOKENS = 1024
CONFIDENCE_THRESHOLD = 0.6

DECISION_DRAFT = "draft"
DECISION_ESCALATE = "escalate"
DECISION_SILENT = "silent"

_VALID_INTENTS = set(MessageIntent.values)
_SILENT_INTENTS = {MessageIntent.JUNK, MessageIntent.AUTO_REPLY}


@dataclass(frozen=True)
class Classification:
    intent: str
    extracted: dict
    action_items: list[dict]
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
    return Classification(
        intent=intent,
        extracted=extracted if isinstance(extracted, dict) else {},
        action_items=[item for item in (action_items or []) if isinstance(item, dict)],
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


def classify_message(message: EmailMessage) -> tuple[Classification, dict]:
    """Classify one inbound message. Raises on an unrecoverable LLM failure."""
    thread = message.thread
    profile = _profile_for(thread.vendor)
    instructions = prompts.compile_vendor_instructions(profile)
    body = prompts.load_prompt_body(BriefPromptSlug.EMAIL_CLASSIFICATION)
    system_prompt = prompts.fill_instructions(body, instructions)

    _nonce, wrapped_body = safety.wrap_untrusted(message.body_clean)
    user_block = (
        f"From: {message.from_email}\nSubject: {message.subject}\n\n{wrapped_body}"
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
    return coerce_classification(raw), prompts.trace_entry("classify", response)


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
        thread.state = ThreadState.PAUSED
        thread.paused_until = classification.pause_until
        thread.save(update_fields=["state", "paused_until", "updated_at"])

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


def wire_lead(message: EmailMessage, classification: Classification) -> Brief | None:
    """Create a canonical lead for a new order, once per thread, without a dup.

    Only an ``order`` on a thread that has no project yet becomes a lead. The
    thread row is locked and re-checked so a crashed retry or a second order on
    the same thread cannot spawn a second lead. A question/follow-up/edits rides
    the already-stitched thread and never creates one.
    """
    if classification.intent != MessageIntent.ORDER:
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
    if classification.intent in _SILENT_INTENTS:
        return DECISION_SILENT
    if classification.confidence < CONFIDENCE_THRESHOLD:
        return DECISION_ESCALATE
    if not classification.safe_to_send:
        return DECISION_ESCALATE
    if classification.escalate_reason:
        return DECISION_ESCALATE
    return DECISION_DRAFT
