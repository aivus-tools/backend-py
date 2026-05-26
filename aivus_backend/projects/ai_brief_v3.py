"""AI brief flow v3.

Single unified chat engine replacing the v2 LangGraph.

- `process_brief_turn` handles every chat turn (first message and follow-ups)
  using one system prompt assembled from BriefPrompt rows in DB.
- `generate_final_documents` produces two final documents at finalize time:
  Production Brief (with embedded deliverables section) and Vendor Outreach
  Email. Deliverables as a standalone document has been folded into the brief.
- `generate_brief_title` calls a cheap/fast model to name the brief after
  finalization so the dashboard doesn't show "Untitled".
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
from django.utils import timezone

from aivus_backend.core.llm import FALLBACK_CHAIN
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
TITLE_MODEL = "gemini-2.5-flash"
MAIN_MAX_TOKENS = 2500
FINALIZATION_MAX_TOKENS = 6000
TITLE_MAX_TOKENS = 80
MAIN_TEMPERATURE = 0.7
FINALIZATION_TEMPERATURE = 0.5
TITLE_TEMPERATURE = 0.4
TITLE_MAX_LENGTH = 80

_HISTORY_KINDS_FOR_LLM = {"chat"}

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
    code = (doc_language or "").lower()
    rule = (
        "=== LANGUAGE & MARKET ===\n"
        f"Brief document language: {name} (frozen — never translate final brief).\n"
        f"Reply language is {name}. ALWAYS write every reply in {name}, even if the\n"
        f"user's message arrives in another language, mixes languages, or contains\n"
        "only numbers, short acknowledgements, or transcribed text that looks like a\n"
        f"different language. Never switch the reply language mid-conversation — the\n"
        f"brief was started in {name} and stays in {name}. Section/brief text always\n"
        "stays in the frozen document language.\n"
    )
    if code and code != "en":
        rule += (
            "The MASTER BRIEF TEMPLATE is provided in English for reference only. "
            "In the final brief, translate ALL section headers, subsection titles, "
            f"field labels, bracketed hints, and enum values into {name}. "
            "No English words, phrases, or headings must remain in the final brief — "
            f"the document must be entirely in {name}, ready to paste into a Word "
            "file. Industry acronyms (SAG, AICP, IATSE, MSA, RTB, SMP, etc.) may "
            f"stay in their original form when no natural {name} equivalent exists.\n"
        )
    return rule


def _build_auth_rule(*, is_anonymous: bool, is_finalized: bool) -> str:
    if is_anonymous:
        return (
            "=== USER AUTH CONTEXT ===\n"
            "The user is browsing anonymously and has NOT signed up yet.\n"
            "When the brief is ready, briefly congratulate the user and ask them\n"
            "to sign up to receive the final package. There is NO 'Finalize'\n"
            "button — never mention any button. Do NOT reveal the contents of\n"
            "the future brief in chat: no excerpts, no field values, no preview\n"
            "of the production brief, vendor email, or deliverables checklist.\n"
            "The reply at this stage is a short congratulation plus a clear\n"
            "sign-up CTA in the user's reply language. Nothing more.\n"
        )
    if is_finalized:
        return (
            "=== USER AUTH CONTEXT ===\n"
            "The user is signed in. The brief has ALREADY been finalized and\n"
            "the document package exists. You apply targeted edits to the\n"
            "documents directly via your own tools — the user does NOT need to\n"
            "press any button for your edits to take effect. Never tell the\n"
            "user to click 'Regenerate', 'Finalize', or any other UI button to\n"
            "apply your changes. If the user asks for a full rebuild of the\n"
            "whole package from scratch, say plainly that a full rebuild is a\n"
            "separate action available in the interface and return no edits;\n"
            "do not name the button. Never tell the user to sign up.\n"
        )
    return (
        "=== USER AUTH CONTEXT ===\n"
        "The user is signed in.\n"
        "When the brief is ready, briefly say so and tell the user the final\n"
        "package is being generated right now — the system starts it\n"
        "automatically. There is NO 'Finalize' button anywhere in the\n"
        "interface; never instruct the user to click any button to begin\n"
        "generation. Never tell signed-in users to register or sign up.\n"
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


def _current_date_iso() -> str:
    return timezone.now().date().isoformat()


def _build_date_rule() -> str:
    return (
        f"Today's date is {_current_date_iso()}. "
        "Use this date for any 'Current Date' or similar date fields in the brief. "
        "Never substitute a year from training data."
    )


def _build_system_prompt(  # noqa: PLR0913
    main_body: str,
    master_template_body: str,
    archetypes_body: str,
    language_rule: str,
    market_rule: str,
    auth_rule: str = "",
    date_rule: str = "",
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
    if date_rule.strip():
        parts.append(date_rule.strip())
    if auth_rule.strip():
        parts.append(auth_rule.strip())
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
    on every subsequent chat turn.

    Post-finalize feedback exchanges (kind != 'chat') are skipped so the LLM
    never sees them as part of the brief conversation.
    """
    out: list[dict[str, Any]] = []
    for msg in history:
        kind = getattr(msg, "kind", "chat") or "chat"
        if kind not in _HISTORY_KINDS_FOR_LLM:
            continue
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
        auth_rule=_build_auth_rule(
            is_anonymous=brief.client_id is None,
            is_finalized=brief.conversation_status == "finalized",
        ),
        date_rule=_build_date_rule(),
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

    if isinstance(parsed, list):
        parsed = next(
            (item for item in parsed if isinstance(item, dict) and item.get("reply")),
            {},
        )
    if not isinstance(parsed, dict):
        parsed = {}

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


# ---------------------------------------------------------------------------
# Post-finalize turn: targeted edits via virtual function calling
# ---------------------------------------------------------------------------

FINALIZED_EDIT_MAX_TOKENS = 4000
FINALIZED_EDIT_TEMPERATURE = 0.3

_EDITABLE_DOCUMENTS = {"production_brief", "vendor_email"}

_FINALIZED_EDIT_INSTRUCTIONS_EN = """
=== POST-FINALIZE EDIT MODE ===
The brief has been finalized. Two documents exist: `production_brief` (HTML)
and `vendor_email` (HTML). Their current texts are provided below in
<CURRENT_DOCUMENTS>.

Your job on every turn:
1. If the user asks you to change something inside a document, return one or
   more `edits` describing the exact change. DO NOT rewrite whole documents
   unless the user explicitly asks for a full rewrite of a section.
2. If the user is just chatting or asking questions, return `edits: []` and
   answer naturally in `reply`.

Available edit tools:
- replace_text — replace an exact HTML fragment with a new one. Use for small
  targeted changes (brand name, single sentence, a date). Field `find` MUST be
  an exact substring of the current document HTML (case-sensitive, whitespace-
  sensitive). If you cannot guarantee an exact unique match, use
  rewrite_section instead.
- rewrite_section — replace a whole section identified by its heading text.
  Use when the user asks to redo or expand a specific block (e.g. "rewrite
  the deliverables", "add more detail to audience"). Field `section_heading`
  is the visible text of an H2 or H3 tag. The section spans from that heading
  up to (but not including) the next heading of the same or higher level, or
  the end of the document.

Output STRICT JSON (no markdown fences). Schema:
{
  "reply": "short chat reply in the user's language, confirming what you did",
  "edits": [
    {
      "tool": "replace_text" | "rewrite_section",
      "document": "production_brief" | "vendor_email",
      "find": "<exact HTML fragment>",       // only for replace_text
      "replace": "<new HTML fragment>",      // only for replace_text
      "section_heading": "<heading text>",   // only for rewrite_section
      "new_html": "<new section HTML incl. heading>",  // only for rewrite_section
      "reason": "one short phrase, optional"
    }
  ]
}

Rules:
- Keep the rest of each document untouched. Only the diff you declare changes.
- Preserve existing HTML structure and classes. Do not strip inline tags.
- Match the document language. The document language is frozen; translate
  user-provided replacements into it if needed.
- Never tell the user to click any button. Your edits apply automatically.
  If the user asks for a full rebuild of the whole package from scratch,
  reply plainly that a full rebuild is a separate action in the interface
  (without naming the button) and return `edits: []`.
- If you cannot find what the user referenced, return `edits: []` and ask
  a clarifying question in `reply`.
""".strip()


def _build_finalized_edit_rule(doc_language: str) -> str:
    return _FINALIZED_EDIT_INSTRUCTIONS_EN


def _current_documents_block(documents: dict[str, BriefFinalDocument]) -> str:
    chunks = ["<CURRENT_DOCUMENTS>"]
    for kind in ("production_brief", "vendor_email"):
        doc = documents.get(kind)
        html = (doc.html if doc else "") or ""
        chunks.append(f'<DOC kind="{kind}">')
        chunks.append(html)
        chunks.append("</DOC>")
    chunks.append("</CURRENT_DOCUMENTS>")
    return "\n".join(chunks)


def _apply_replace_text(html: str, find: str, replace: str) -> tuple[str, bool]:
    if not find:
        return html, False
    occurrences = html.count(find)
    if occurrences != 1:
        return html, False
    return html.replace(find, replace, 1), True


def _apply_rewrite_section(
    html: str,
    section_heading: str,
    new_html: str,
) -> tuple[str, bool]:
    if not section_heading or not new_html:
        return html, False
    heading_pattern = re.compile(
        r"<(h[1-6])\b[^>]*>\s*" + re.escape(section_heading.strip()) + r"\s*</\1>",
        re.IGNORECASE,
    )
    match = heading_pattern.search(html)
    if not match:
        return html, False
    level = int(match.group(1)[1])
    start = match.start()
    search_from = match.end()
    next_heading_pattern = re.compile(
        r"<h([1-" + str(level) + r"])\b[^>]*>",
        re.IGNORECASE,
    )
    next_match = next_heading_pattern.search(html, search_from)
    end = next_match.start() if next_match else len(html)
    updated = html[:start] + new_html.strip() + html[end:]
    return updated, True


def _apply_edits(
    documents: dict[str, BriefFinalDocument],
    edits: list[dict[str, Any]],
) -> list[BriefFinalDocument]:
    """Apply edits to the in-memory document objects. Returns the list of
    documents whose html actually changed, in stable order."""
    changed: dict[str, BriefFinalDocument] = {}
    for edit in edits:
        if not isinstance(edit, dict):
            continue
        document_kind = str(edit.get("document") or "").strip()
        tool = str(edit.get("tool") or "").strip()
        if document_kind not in _EDITABLE_DOCUMENTS:
            continue
        document = documents.get(document_kind)
        if document is None:
            continue

        if tool == "replace_text":
            updated, ok = _apply_replace_text(
                document.html or "",
                str(edit.get("find") or ""),
                str(edit.get("replace") or ""),
            )
        elif tool == "rewrite_section":
            updated, ok = _apply_rewrite_section(
                document.html or "",
                str(edit.get("section_heading") or ""),
                str(edit.get("new_html") or ""),
            )
        else:
            continue

        if not ok or updated == document.html:
            continue

        document.html = sanitize_html(updated)
        changed[document_kind] = document

    return list(changed.values())


def process_finalized_turn(
    brief: Brief,
    user_message: str,
    attachments: list[BriefAttachment] | None = None,
    history: list[ChatMessage] | None = None,
) -> dict[str, Any]:
    attachments = attachments or []
    history = history or []

    doc_language = brief.document_language or "en"

    main_body = _load_prompt_body("main_system_prompt")
    master_body = _load_prompt_body("master_brief_template")
    archetypes_body = _load_prompt_body("archetypes_reference")
    model = _model_for_prompt("main_system_prompt")

    base_system_prompt = _build_system_prompt(
        main_body=main_body,
        master_template_body=master_body,
        archetypes_body=archetypes_body,
        language_rule=_build_language_rule(doc_language),
        market_rule=_build_market_rule(doc_language),
        auth_rule=_build_auth_rule(
            is_anonymous=brief.client_id is None,
            is_finalized=True,
        ),
        date_rule=_build_date_rule(),
    )
    edit_rule = _build_finalized_edit_rule(doc_language)
    system_prompt = f"{base_system_prompt}\n\n{edit_rule}"

    document_qs = BriefFinalDocument.objects.filter(
        brief=brief, kind__in=list(_EDITABLE_DOCUMENTS)
    )
    documents: dict[str, BriefFinalDocument] = {doc.kind: doc for doc in document_qs}

    user_parts = _build_user_parts(user_message, attachments)
    user_parts.append({"type": "text", "text": _current_documents_block(documents)})

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
    ]
    messages.extend(_build_history_messages(history))
    messages.append({"role": "user", "content": user_parts})

    try:
        parsed, response = call_llm_json(
            model=model,
            messages=messages,
            temperature=FINALIZED_EDIT_TEMPERATURE,
            max_tokens=FINALIZED_EDIT_MAX_TOKENS,
        )
    except ValueError:
        logger.warning(
            "Finalized JSON parse failed, plain fallback for brief %s", brief.id
        )
        response = call_llm(
            model=model,
            messages=messages,
            temperature=FINALIZED_EDIT_TEMPERATURE,
            max_tokens=FINALIZED_EDIT_MAX_TOKENS,
            json_mode=False,
        )
        parsed = _salvage_reply(response.content)

    if isinstance(parsed, list):
        parsed = next((x for x in parsed if isinstance(x, dict) and x.get("reply")), {})
    if not isinstance(parsed, dict):
        parsed = {}

    reply = str(parsed.get("reply", "")).strip() or _fallback_reply(doc_language)
    raw_edits = parsed.get("edits") or []
    if not isinstance(raw_edits, list):
        raw_edits = []

    updated_documents = _apply_edits(documents, raw_edits)
    for document in updated_documents:
        document.plain_text = _strip_html(document.html)
        document.save(update_fields=["html", "plain_text", "updated_at"])

    return {
        "reply": reply,
        "ready_to_finalize": False,
        "conversation_status": "finalized",
        "document_language": doc_language,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "cost_usd": response.cost_usd,
        "model_used": response.model_used,
        "traces": [_trace_entry("finalized_chat", response)],
        "updated_documents": updated_documents,
    }


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

    doc_language = brief.document_language or "en"

    system_prompt = _build_system_prompt(
        main_body=main_body,
        master_template_body=master_body,
        archetypes_body=archetypes_body,
        language_rule=_build_language_rule(doc_language),
        market_rule=_build_market_rule(doc_language),
        date_rule=_build_date_rule(),
    )

    finalization_text = finalization_body.strip() or "Please finalize the brief now."
    finalization_text = (
        f"Today's date is {_current_date_iso()}. "
        "Use this date for the 'Current Date' field in the brief.\n\n"
        f"{finalization_text}"
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
                    "text": finalization_text,
                }
            ],
        }
    )

    parsed: Any = None
    response: LLMResponse | None = None
    last_error: Exception | None = None
    models_to_try = [model]
    fallback = FALLBACK_CHAIN.get(model)
    while fallback and fallback not in models_to_try:
        models_to_try.append(fallback)
        fallback = FALLBACK_CHAIN.get(fallback)
    for attempt_model in models_to_try:
        try:
            parsed, response = call_llm_json(
                model=attempt_model,
                messages=messages,
                temperature=FINALIZATION_TEMPERATURE,
                max_tokens=FINALIZATION_MAX_TOKENS,
            )
            break
        except ValueError as exc:
            logger.warning(
                "Finalization JSON parse failed: model=%s brief=%s err=%s",
                attempt_model,
                brief.id,
                exc,
            )
            last_error = exc
            parsed = None
            response = None

    if response is None or parsed is None:
        msg = "Finalization failed: all models returned invalid JSON"
        raise last_error or ValueError(msg)

    if isinstance(parsed, list):
        parsed = next(
            (
                item
                for item in parsed
                if isinstance(item, dict) and item.get("production_brief_html")
            ),
            {},
        )
    if not isinstance(parsed, dict):
        parsed = {}

    production_brief_html = sanitize_html(
        str(parsed.get("production_brief_html", "")).strip()
    )
    vendor_email_html = sanitize_html(str(parsed.get("vendor_email_html", "")).strip())
    vendor_email_text = str(parsed.get("vendor_email_text", "")).strip()

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

    return {
        "documents": documents,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "cost_usd": response.cost_usd,
        "model_used": response.model_used,
        "traces": [_trace_entry("finalize", response)],
    }


# ---------------------------------------------------------------------------
# Post-finalize helpers: brief title + feedback request
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_TITLE_TRIM_CHARS = " .,:;!?\t\r\n\"'«»“”‘’—–-"


def _strip_html(html: str) -> str:
    if not html:
        return ""
    text = _TAG_RE.sub(" ", html)
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip()


def _sanitize_title(raw: str) -> str:
    if not raw:
        return ""
    text = raw.strip().splitlines()[0] if raw.strip() else ""
    text = _WHITESPACE_RE.sub(" ", text).strip()
    text = text.strip(_TITLE_TRIM_CHARS)
    if len(text) > TITLE_MAX_LENGTH:
        text = text[:TITLE_MAX_LENGTH].rstrip(_TITLE_TRIM_CHARS)
    return text


def generate_brief_title(brief: Brief) -> str:
    """Ask a fast/cheap model to name the brief. Returns empty string on
    failure — callers must tolerate that."""
    history = list(brief.chat_messages.order_by("created_at"))
    first_user_msg = next(
        (m.content for m in history if m.role == "user" and m.content), ""
    )
    production_brief = (
        BriefFinalDocument.objects.filter(brief=brief, kind="production_brief")
        .order_by("-created_at")
        .first()
    )
    brief_text = _strip_html(production_brief.html if production_brief else "")[:4000]

    doc_language = brief.document_language or "en"
    language_name = _language_name(doc_language)
    system_prompt = (
        "You name client video production brief projects. "
        f"Return a short 3-6 word title in {language_name} — the same language as "
        "the conversation. No quotes, no trailing punctuation, no prefixes like "
        '"Title:". Just the title itself.'
    )

    user_text_parts = []
    if first_user_msg:
        user_text_parts.append(f"Client's first ask:\n{first_user_msg.strip()}")
    if brief_text:
        user_text_parts.append(f"Production brief excerpt:\n{brief_text}")
    if not user_text_parts:
        return ""

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "\n\n".join(user_text_parts)},
            ],
        },
    ]

    response = call_llm(
        model=TITLE_MODEL,
        messages=messages,
        temperature=TITLE_TEMPERATURE,
        max_tokens=TITLE_MAX_TOKENS,
    )
    return _sanitize_title(response.content or "")


FEEDBACK_QUESTION_TEXT = {
    "en": (
        "Your brief is ready! Quick pulse-check so we can keep making this better:\n"
        "1) Was everything clear along the way?\n"
        "2) How useful is the brief for you right now?\n"
        "3) Anything missing or confusing?\n"
        "\n"
        "Drop everything in a single reply — no pressure."
    ),
    "ru": (
        "Бриф готов! Хочу коротко узнать впечатления, чтобы дальше делать ещё лучше:\n"
        "1) Всё ли было понятно в процессе?\n"
        "2) Насколько полезен получился бриф?\n"
        "3) Чего не хватило или что смутило?\n"
        "\n"
        "Ответь одним сообщением — без напряга."
    ),
}

FEEDBACK_ACK_TEXT = {
    "en": (
        "Thanks, got it — passed your feedback to the team. "
        "If anything else comes up, just ping me here."
    ),
    "ru": ("Спасибо, передал фидбек команде. Если ещё что-то всплывёт — пиши сюда же."),
}


def feedback_question_for(language: str) -> str:
    return FEEDBACK_QUESTION_TEXT.get(
        (language or "en").lower(), FEEDBACK_QUESTION_TEXT["en"]
    )


def feedback_ack_for(language: str) -> str:
    return FEEDBACK_ACK_TEXT.get((language or "en").lower(), FEEDBACK_ACK_TEXT["en"])
