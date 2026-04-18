"""AI brief flow v3.

Single unified chat engine replacing the v2 LangGraph.

- `process_brief_turn` handles every chat turn (first message and follow-ups)
  using one system prompt assembled from BriefPrompt rows in DB.
- `generate_final_documents` produces the three final deliverables at finalize
  time: Production Brief, Vendor Outreach Email, Deliverables Checklist.
"""

# ruff: noqa: RUF001

from __future__ import annotations

import json
import logging
import mimetypes
import re
from dataclasses import dataclass
from typing import Any

from django.conf import settings

from aivus_backend.core.llm import LLMResponse
from aivus_backend.core.llm import call_llm
from aivus_backend.core.llm import call_llm_json
from aivus_backend.core.sanitize import sanitize_html
from aivus_backend.projects.models import Brief
from aivus_backend.projects.models import BriefAttachment
from aivus_backend.projects.models import BriefFinalDocument
from aivus_backend.projects.models import BriefPrompt
from aivus_backend.projects.models import ChatMessage

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3.1-pro-preview"
MAIN_MAX_TOKENS = 2500
FINALIZATION_MAX_TOKENS = 6000
MAIN_TEMPERATURE = 0.7
FINALIZATION_TEMPERATURE = 0.5

_CYRILLIC_RE = re.compile(r"[\u0400-\u04ff]")
_HIRAGANA_KATAKANA_RE = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")
_HANGUL_RE = re.compile(r"[\uac00-\ud7af\u1100-\u11ff]")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

_LANGUAGE_NAMES = {
    "en": "English",
    "ru": "Russian",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
}


@dataclass
class ChatTurnResult:
    reply: str
    ready_to_finalize: bool
    conversation_status: str
    document_language: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    model_used: str
    traces: list[dict]

    def to_dict(self) -> dict[str, Any]:
        return {
            "reply": self.reply,
            "ready_to_finalize": self.ready_to_finalize,
            "conversation_status": self.conversation_status,
            "document_language": self.document_language,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": self.cost_usd,
            "model_used": self.model_used,
            "traces": self.traces,
        }


def detect_language(text: str) -> str:
    if not text:
        return ""
    if _CYRILLIC_RE.search(text):
        return "ru"
    if _HIRAGANA_KATAKANA_RE.search(text):
        return "ja"
    if _HANGUL_RE.search(text):
        return "ko"
    if _CJK_RE.search(text):
        return "zh"
    return ""


def _resolve_language(
    user_message: str,
    history: list[ChatMessage],
    fallback: str,
) -> str:
    detected = detect_language(user_message)
    if detected:
        return detected
    for msg in reversed(history):
        detected = detect_language(msg.content)
        if detected:
            return detected
    return fallback or "en"


def _language_name(code: str) -> str:
    return _LANGUAGE_NAMES.get((code or "").lower(), "English")


def _build_language_rule(doc_language: str) -> str:
    name = _language_name(doc_language)
    return (
        "=== LANGUAGE & MARKET ===\n"
        f"Brief document language: {name} (frozen — never translate final brief).\n"
        "Reply language: match the user's latest message language, even if it differs\n"
        "from the brief document language. Section/brief text always stays in the\n"
        "frozen document language.\n"
    )


def _build_market_rule(doc_language: str) -> str:
    code = (doc_language or "").lower()
    if code == "ru":
        return (
            "Market context: Russian Federation. Use rubles (RUB, ₽) for budgets.\n"
            "Reference Russian production realities: tariffs, vendors, typical "
            "day rates, casting agencies, legal framework for rights "
            "(исключительные/неисключительные), cities (Москва, СПб, Сочи).\n"
        )
    if code == "en":
        return (
            "Market context: United States. Use US dollars (USD, $) for budgets.\n"
            "Reference US production realities: SAG/non-union talent, AICP bid form,\n"
            "IATSE crew norms, Buyouts, MSA, common cities (LA, NYC, ATL).\n"
        )
    return "Market context: infer from brief language (ru → RF/rubles, en → US/USD).\n"


def _build_system_prompt(
    main_body: str,
    master_template_body: str,
    archetypes_body: str,
    language_rule: str,
    market_rule: str,
) -> str:
    parts = [main_body.strip()]
    if master_template_body.strip():
        parts.append("=== MASTER BRIEF TEMPLATE (reference) ===")
        parts.append(master_template_body.strip())
    if archetypes_body.strip():
        parts.append("=== PROJECT ARCHETYPES (internal reference) ===")
        parts.append(archetypes_body.strip())
    parts.append(language_rule.strip())
    parts.append(market_rule.strip())
    return "\n\n".join(parts)


def _attachment_to_part(attachment: BriefAttachment) -> dict[str, Any] | None:
    """Build a multimodal Part payload for an attachment.

    Prefers Google Cloud Storage URIs when the default storage is GCS (Vertex
    reads gs:// directly). Falls back to inline bytes for local dev and small
    files.
    """
    mime = attachment.mime_type or mimetypes.guess_type(attachment.filename)[0]
    if not mime:
        mime = "application/octet-stream"

    if getattr(settings, "STORAGE_BACKEND", "local") == "gcs":
        bucket = getattr(settings, "GS_BUCKET_NAME", "")
        if bucket:
            return {
                "type": "file_uri",
                "file_uri": f"gs://{bucket}/{attachment.file.name}",
                "mime_type": mime,
            }

    try:
        with attachment.file.open("rb") as fh:
            data = fh.read()
    except Exception:
        logger.exception("Cannot read attachment %s", attachment.id)
        return None

    return {"type": "inline_bytes", "data": data, "mime_type": mime}


def _build_user_parts(
    user_message: str, attachments: list[BriefAttachment]
) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    if user_message:
        parts.append({"type": "text", "text": user_message})
    for attachment in attachments:
        part = _attachment_to_part(attachment)
        if part:
            parts.append(part)
    if not parts:
        parts.append({"type": "text", "text": ""})
    return parts


def _build_history_messages(history: list[ChatMessage]) -> list[dict[str, Any]]:
    """Rebuild conversation history as multimodal messages. User turns carry
    forward their attachments so the model keeps seeing the referenced files
    on every subsequent chat turn."""
    out: list[dict[str, Any]] = []
    for msg in history:
        role = "assistant" if msg.role == "assistant" else "user"
        parts: list[dict[str, Any]] = [{"type": "text", "text": msg.content}]
        if role == "user" and hasattr(msg, "attachments"):
            for attachment in msg.attachments.all():
                part = _attachment_to_part(attachment)
                if part:
                    parts.append(part)
        out.append({"role": role, "content": parts})
    return out


def _trace_entry(purpose: str, response: LLMResponse) -> dict[str, Any]:
    return {
        "purpose": purpose,
        "model": response.model_used,
        "request_messages": response.request_messages,
        "request_params": response.request_params,
        "response_raw": response.content,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "cost_usd": response.cost_usd,
        "latency_ms": response.latency_ms,
    }


def _load_prompt_body(slug: str) -> str:
    return BriefPrompt.get_active_body(slug=slug, default="")


def _model_for_prompt(slug: str) -> str:
    prompt = BriefPrompt.get_active(slug=slug)
    if prompt and prompt.model_name:
        return prompt.model_name
    return DEFAULT_MODEL


def process_brief_turn(
    brief: Brief,
    user_message: str,
    attachments: list[BriefAttachment] | None = None,
    history: list[ChatMessage] | None = None,
) -> dict[str, Any]:
    attachments = attachments or []
    history = history or []

    doc_language = brief.document_language or _resolve_language(
        user_message, history, fallback=""
    )

    main_body = _load_prompt_body("main_system_prompt")
    master_body = _load_prompt_body("master_brief_template")
    archetypes_body = _load_prompt_body("archetypes_reference")
    model = _model_for_prompt("main_system_prompt")

    system_prompt = _build_system_prompt(
        main_body=main_body,
        master_template_body=master_body,
        archetypes_body=archetypes_body,
        language_rule=_build_language_rule(doc_language),
        market_rule=_build_market_rule(doc_language),
    )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
    ]
    messages.extend(_build_history_messages(history))
    messages.append(
        {"role": "user", "content": _build_user_parts(user_message, attachments)}
    )

    try:
        parsed, response = call_llm_json(
            model=model,
            messages=messages,
            temperature=MAIN_TEMPERATURE,
            max_tokens=MAIN_MAX_TOKENS,
        )
    except ValueError:
        # LLM returned malformed / truncated JSON (happens when Gemini cuts the
        # answer mid-stream). Fall back to a plain-text call and salvage what
        # the model already wrote so the user at least sees a reply.
        logger.warning(
            "JSON parse failed, retrying in plain mode for brief %s", brief.id
        )
        response = call_llm(
            model=model,
            messages=messages,
            temperature=MAIN_TEMPERATURE,
            max_tokens=MAIN_MAX_TOKENS,
            json_mode=False,
        )
        parsed = _salvage_reply(response.content)

    reply = str(parsed.get("reply", "")).strip()
    ready_to_finalize = bool(parsed.get("ready_to_finalize", False))

    if not reply:
        logger.warning("LLM returned empty reply for brief %s", brief.id)
        reply = _fallback_reply(doc_language)

    conversation_status = "ready_to_finalize" if ready_to_finalize else "in_progress"

    return ChatTurnResult(
        reply=reply,
        ready_to_finalize=ready_to_finalize,
        conversation_status=conversation_status,
        document_language=doc_language,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        cost_usd=response.cost_usd,
        model_used=response.model_used,
        traces=[_trace_entry("chat", response)],
    ).to_dict()


def _salvage_reply(content: str) -> dict[str, Any]:
    """Best-effort recovery when Gemini returns malformed/truncated JSON.

    Tries, in order:
      1. strict json.loads of the full content;
      2. json.loads of the first {...} block;
      3. regex-extract the value of the "reply" field;
      4. treat the whole payload as plain text reply.
    """
    text = (content or "").strip()
    if not text:
        return {"reply": "", "ready_to_finalize": False}

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        try:
            parsed = json.loads(brace_match.group(0))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    reply_match = re.search(
        r'"reply"\s*:\s*"((?:\\.|[^"\\])*)',
        text,
        re.DOTALL,
    )
    if reply_match:
        raw = reply_match.group(1)
        try:
            unescaped = json.loads(f'"{raw}"')
        except json.JSONDecodeError:
            unescaped = raw.encode("utf-8").decode("unicode_escape", errors="ignore")
        return {"reply": unescaped.strip(), "ready_to_finalize": False}

    return {"reply": text, "ready_to_finalize": False}


def _fallback_reply(doc_language: str) -> str:
    if doc_language == "ru":
        return (
            "Упс, у меня мелкий сбой — скинь последнее сообщение ещё раз, и я продолжу."
        )
    return "Sorry, I hit a small glitch — could you repeat your last message?"


def generate_final_documents(brief: Brief) -> dict[str, Any]:
    history = list(
        brief.chat_messages.prefetch_related("attachments").order_by("created_at")
    )
    if not history:
        msg = "Cannot finalize brief without chat history"
        raise ValueError(msg)

    main_body = _load_prompt_body("main_system_prompt")
    master_body = _load_prompt_body("master_brief_template")
    archetypes_body = _load_prompt_body("archetypes_reference")
    finalization_body = _load_prompt_body("finalization_prompt")
    model = _model_for_prompt("finalization_prompt")

    doc_language = brief.document_language or _resolve_language(
        history[-1].content if history else "",
        history[:-1],
        fallback="",
    )

    system_prompt = _build_system_prompt(
        main_body=main_body,
        master_template_body=master_body,
        archetypes_body=archetypes_body,
        language_rule=_build_language_rule(doc_language),
        market_rule=_build_market_rule(doc_language),
    )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
    ]
    messages.extend(_build_history_messages(history))
    messages.append(
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": finalization_body.strip()
                    or "Please finalize the brief now.",
                }
            ],
        }
    )

    parsed, response = call_llm_json(
        model=model,
        messages=messages,
        temperature=FINALIZATION_TEMPERATURE,
        max_tokens=FINALIZATION_MAX_TOKENS,
    )

    production_brief_html = sanitize_html(
        str(parsed.get("production_brief_html", "")).strip()
    )
    vendor_email_html = sanitize_html(str(parsed.get("vendor_email_html", "")).strip())
    vendor_email_text = str(parsed.get("vendor_email_text", "")).strip()
    deliverables_html = sanitize_html(
        str(parsed.get("deliverables_checklist_html", "")).strip()
    )

    if not production_brief_html:
        msg = "Finalization LLM did not return production_brief_html"
        raise ValueError(msg)

    documents = []
    BriefFinalDocument.objects.filter(brief=brief).delete()

    documents.append(
        BriefFinalDocument.objects.create(
            brief=brief,
            kind="production_brief",
            html=production_brief_html,
        )
    )
    documents.append(
        BriefFinalDocument.objects.create(
            brief=brief,
            kind="vendor_email",
            html=vendor_email_html,
            plain_text=vendor_email_text,
        )
    )
    documents.append(
        BriefFinalDocument.objects.create(
            brief=brief,
            kind="deliverables_checklist",
            html=deliverables_html,
        )
    )

    return {
        "documents": documents,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "cost_usd": response.cost_usd,
        "model_used": response.model_used,
        "traces": [_trace_entry("finalize", response)],
    }
