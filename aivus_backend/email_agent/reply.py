"""First-response engine for inbound email (Stage 3, S3-27/28/29).

The engine fills a safe skeleton rather than writing a letter from scratch: the
model supplies prose slots in the client's language, and code assembles the
skeleton, inserts the brief link, scans for forbidden commitments and strips any
exfiltration URL. It never chooses recipients — pinning happens at send time.
The output is a draft awaiting human approval (MVP is draft-only); the autonomy
layer (S3-20) owns whether it is ever sent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import IntegrityError
from django.db import transaction
from django.utils import timezone

from aivus_backend.core.enums import BriefPromptSlug
from aivus_backend.core.llm import call_llm_json
from aivus_backend.email_agent import notifications
from aivus_backend.email_agent import prompts
from aivus_backend.email_agent import safety
from aivus_backend.email_agent.events import NotificationEvent
from aivus_backend.email_agent.models import AgentLog
from aivus_backend.email_agent.models import OutboundDraft
from aivus_backend.email_agent.models import OutboundDraftKind
from aivus_backend.email_agent.models import OutboundDraftStatus
from aivus_backend.email_agent.models import VendorAgentProfile

if TYPE_CHECKING:
    from aivus_backend.email_agent.classification import Classification
    from aivus_backend.email_agent.models import EmailMessage
    from aivus_backend.email_agent.models import EmailThread

REPLY_TEMPERATURE = 0.4
REPLY_MAX_TOKENS = 800
DRAFT_TTL = timedelta(hours=48)

VARIANT_A = "A"
VARIANT_B = "B"
VARIANT_C = "C"

_ACTION_BY_VARIANT = {
    VARIANT_A: "acknowledge_receipt",
    VARIANT_B: "send_brief_link",
    VARIANT_C: "say_producer_will_join",
}

_VARIANT_GUIDE = {
    VARIANT_A: "Confirm you received the email. Do not send a brief link.",
    VARIANT_B: "Ask the client to fill the brief; the system adds the link itself.",
    VARIANT_C: "Acknowledge the urgency; the producer is being alerted separately.",
}

_SLOT_KEYS = ("greeting", "main", "next_step", "signoff")

FORBIDDEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"[$€£]\s?\d"),
    re.compile(r"\b\d+\s?(?:usd|eur|dollars?)\b", re.IGNORECASE),
    re.compile(r"\b\d+\s?%\s?(?:off|discount)", re.IGNORECASE),
    re.compile(r"\b\d+\s?(?:business\s+)?(?:days?|weeks?|hours?)\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class ReplyProposal:
    variant: str
    action: str
    body: str
    language: str
    confidence: float


def _frontend_url() -> str:
    return getattr(settings, "FRONTEND_URL", "https://go.aivus.co").rstrip("/")


def build_brief_link(thread: EmailThread) -> str:
    """The vendor's personal brief link carrying this lead's thread token.

    Returns "" whenever the chain is incomplete (no project, no slug, no brief
    token), so the caller degrades to variant A instead of sending a broken link.
    The token lets the filled brief attach to this same lead rather than spawning
    a duplicate.
    """
    project = thread.project
    if project is None:
        return ""
    settings_row = getattr(project.vendor, "vendor_settings", None)
    slug = settings_row.slug if settings_row is not None else None
    if not slug:
        return ""
    brief = getattr(project, "brief", None)
    if brief is None or not brief.anonymous_token:
        return ""
    return f"{_frontend_url()}/brief/{slug}?b={brief.id}&t={brief.anonymous_token}"


def decide_variant(classification: Classification, *, has_brief_link: bool) -> str:
    extracted = classification.extracted
    if classification.urgent or (extracted.get("budget") and extracted.get("deadline")):
        return VARIANT_C
    missing = extracted.get("missing")
    wants = extracted.get("wants")
    if (missing or not wants) and has_brief_link:
        return VARIANT_B
    return VARIANT_A


def has_forbidden_commitments(text: str) -> bool:
    return any(pattern.search(text) for pattern in FORBIDDEN_PATTERNS)


def _render_skeleton(slots: dict, brief_link: str) -> str:
    greeting = str(slots.get("greeting", "")).strip()
    main = str(slots.get("main", "")).strip()
    next_step = str(slots.get("next_step", "")).strip()
    signoff = str(slots.get("signoff", "")).strip()
    parts = [greeting, main]
    if brief_link:
        parts.append(brief_link)
    parts.extend([next_step, signoff])
    return "\n\n".join(part for part in parts if part)


def _build_user_block(
    classification: Classification, variant: str, wrapped_body: str
) -> str:
    extracted = classification.extracted
    return (
        f"Intent: {classification.intent}\n"
        f"Variant: {variant} — {_VARIANT_GUIDE[variant]}\n"
        f"Client wants: {extracted.get('wants', '')}\n"
        f"Missing: {extracted.get('missing', '')}\n\n"
        f"Fill the reply slots for this email:\n{wrapped_body}"
    )


def propose_reply(
    message: EmailMessage, classification: Classification
) -> tuple[ReplyProposal | None, dict]:
    """Draft a reply body, or None when it must escalate instead."""
    thread = message.thread
    profile = _profile_for(thread)
    instructions = prompts.compile_vendor_instructions(profile)
    body = prompts.load_prompt_body(BriefPromptSlug.EMAIL_REPLY)
    system_prompt = prompts.fill_instructions(body, instructions)

    brief_link = build_brief_link(thread)
    variant = decide_variant(classification, has_brief_link=bool(brief_link))
    _nonce, wrapped_body = safety.wrap_untrusted(message.body_clean)
    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": _build_user_block(classification, variant, wrapped_body),
        },
    ]
    raw, response = call_llm_json(
        model=prompts.model_for_prompt(BriefPromptSlug.EMAIL_REPLY),
        messages=messages,
        temperature=REPLY_TEMPERATURE,
        max_tokens=REPLY_MAX_TOKENS,
    )
    trace = prompts.trace_entry("reply", response)

    slots = raw.get("slots") if isinstance(raw.get("slots"), dict) else raw
    if not isinstance(slots, dict):
        return None, trace

    link_for_body = brief_link if variant == VARIANT_B else ""
    rendered = _render_skeleton(slots, link_for_body)
    if not rendered.strip():
        return None, trace
    if has_forbidden_commitments(rendered):
        return None, trace

    allowed_urls = (brief_link,) if link_for_body else ()
    clean_body = safety.sanitize_outbound(rendered, allowed_urls=allowed_urls)
    language = classification.language or _normalize_lang(raw.get("language"))
    proposal = ReplyProposal(
        variant=variant,
        action=_ACTION_BY_VARIANT[variant],
        body=clean_body,
        language=language,
        confidence=classification.confidence,
    )
    return proposal, trace


def create_draft(
    message: EmailMessage, proposal: ReplyProposal
) -> OutboundDraft | None:
    """Queue the proposal as a pending draft, deduped by the pending constraint."""
    thread = message.thread
    try:
        with transaction.atomic():
            draft = OutboundDraft.objects.create(
                thread=thread,
                in_reply_to_message=message,
                kind=OutboundDraftKind.FIRST_REPLY,
                body=proposal.body,
                status=OutboundDraftStatus.PENDING,
                expires_at=timezone.now() + DRAFT_TTL,
                metadata={
                    "variant": proposal.variant,
                    "action": proposal.action,
                    "confidence": proposal.confidence,
                    "language": proposal.language,
                },
            )
    except IntegrityError:
        return None
    AgentLog.objects.create(
        thread=thread,
        project=thread.project,
        event="draft_created",
        payload={"variant": proposal.variant, "action": proposal.action},
    )
    return draft


def handle_reply(
    message: EmailMessage, classification: Classification
) -> OutboundDraft | None:
    """Full reply flow: draft, hand off, and alert the producer.

    A blocked or empty draft escalates instead of going silent. Variant C fires
    an extra urgent-lead notification. The draft only reaches the client after a
    human approves it (S3-20 owns the send).
    """
    thread = message.thread
    vendor = thread.vendor
    proposal, _trace = propose_reply(message, classification)
    if proposal is None:
        AgentLog.objects.create(thread=thread, event="reply_blocked", payload={})
        notifications.notify(
            vendor,
            NotificationEvent.ESCALATION,
            {"lines": [f"Subject: {message.subject}"]},
            dedup_key=f"reply_blocked:{message.id}",
        )
        return None

    draft = create_draft(message, proposal)
    if draft is None:
        return None

    _dashboard_link = f"{_frontend_url()}/app/email-agent"
    notifications.notify(
        vendor,
        NotificationEvent.DRAFT_CREATED,
        {"lines": [f"Subject: {message.subject}"], "cta_url": _dashboard_link},
        dedup_key=str(message.id),
    )
    if proposal.variant == VARIANT_C:
        notifications.notify(
            vendor,
            NotificationEvent.URGENT_LEAD,
            {
                "lines": [
                    f"Subject: {message.subject}",
                    f"Budget: {classification.extracted.get('budget', '')}",
                    f"Deadline: {classification.extracted.get('deadline', '')}",
                ],
                "cta_url": _dashboard_link,
            },
            urgent=True,
            dedup_key=str(message.id),
        )
    return draft


def _profile_for(thread: EmailThread) -> VendorAgentProfile | None:
    return VendorAgentProfile.objects.filter(vendor=thread.vendor).first()


def _normalize_lang(value: object) -> str:
    code = str(value or "").strip().lower()[:2]
    return code or "en"
