"""REST views for AI brief v3."""

from __future__ import annotations

import hmac
import json
import logging
import secrets
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING
from typing import Any

from celery import chain
from celery.result import AsyncResult
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import F
from django.http import HttpResponse
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit

from aivus_backend.core.decorators import public_endpoint
from aivus_backend.core.decorators import require_groups
from aivus_backend.core.enums import BriefSource
from aivus_backend.core.enums import FinalDocumentKind
from aivus_backend.core.enums import ProjectStatus
from aivus_backend.core.sanitize import sanitize_html
from aivus_backend.core.slugs import normalize_slug
from aivus_backend.projects import stt
from aivus_backend.projects.ai_brief_v3 import feedback_ack_for
from aivus_backend.projects.ai_brief_v3 import process_brief_turn
from aivus_backend.projects.ai_brief_v3 import process_finalized_turn
from aivus_backend.projects.api.serializers import serialize_brief_attachment
from aivus_backend.projects.api.serializers import serialize_brief_feedback
from aivus_backend.projects.api.serializers import serialize_brief_final_document
from aivus_backend.projects.api.serializers import serialize_brief_share
from aivus_backend.projects.api.serializers import serialize_brief_share_public
from aivus_backend.projects.api.serializers import serialize_brief_v3
from aivus_backend.projects.api.serializers import serialize_brief_v3_detail
from aivus_backend.projects.api.serializers import serialize_brief_v3_list_item
from aivus_backend.projects.api.serializers import serialize_chat_message_v3
from aivus_backend.projects.attachments import ALLOWED_MIME_TYPES
from aivus_backend.projects.attachments import DOCX_DETECTED_ALIASES
from aivus_backend.projects.attachments import DOCX_MIME
from aivus_backend.projects.attachments import MAX_ATTACHMENT_SIZE_BYTES
from aivus_backend.projects.attachments import sniff_mime
from aivus_backend.projects.models import Brief
from aivus_backend.projects.models import BriefAttachment
from aivus_backend.projects.models import BriefFeedback
from aivus_backend.projects.models import BriefFinalDocument
from aivus_backend.projects.models import BriefShare
from aivus_backend.projects.models import ChatMessage
from aivus_backend.projects.models import Project
from aivus_backend.projects.tasks import clear_brief_pending_task
from aivus_backend.projects.tasks import finalize_brief_task
from aivus_backend.projects.tasks import generate_first_reply_task
from aivus_backend.projects.tasks import import_wix_attachments_task
from aivus_backend.projects.tasks import mark_brief_send_failed_task
from aivus_backend.projects.tasks import mark_project_sent_task
from aivus_backend.projects.tasks import persist_message_traces
from aivus_backend.projects.tasks import send_emails_task
from aivus_backend.projects.tasks import set_brief_pending_task
from aivus_backend.users.models import Client
from aivus_backend.users.models import User
from aivus_backend.users.models import Vendor
from aivus_backend.users.models import VendorSettings

if TYPE_CHECKING:
    from django.core.files.uploadedfile import UploadedFile

logger = logging.getLogger(__name__)

MESSAGE_LIMIT_AUTH = 100
MESSAGE_LIMIT_ANON = 50
MAX_BRIEF_COST_USD = Decimal("5.00")
MAX_MESSAGE_LENGTH = 10000
MAX_FEEDBACK_COMMENT_LENGTH = 2000
MAX_FINAL_DOCUMENT_HTML_LENGTH = 200_000
MAX_ATTACHMENTS_PER_BRIEF_AUTH = 10
MAX_ATTACHMENTS_PER_BRIEF_ANON = 3
VALID_FEEDBACK_RATINGS = {"up", "down"}

# The white-label anonymous flow shows the brief to an unauthenticated visitor on
# the vendor's branded page. The vendor outreach email (kind=vendor_email) carries
# the vendor's outreach strategy and contacts — vendor PII the client must not see
# (PRD §5). Anonymous token reads/edits are restricted to these client-facing
# kinds; vendor_email is exposed only to the authenticated owner of the brief.
ANON_VISIBLE_DOCUMENT_KINDS = (
    FinalDocumentKind.PRODUCTION_BRIEF,
    FinalDocumentKind.DELIVERABLES_CHECKLIST,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def conditional_ratelimit(**ratelimit_kwargs):
    def decorator(func):
        if getattr(settings, "RATELIMIT_ENABLE", True):
            return ratelimit(**ratelimit_kwargs)(func)
        return func

    return decorator


def resolve_client_ip(request) -> str:
    """Single source of truth for the visitor's IP behind the reverse proxy.

    django-ratelimit's built-in ``key="ip"`` reads ``REMOTE_ADDR``, which behind
    Traefik / the Next.js proxy is the proxy's own IP — every public visitor
    collapses into one bucket and a single abuser throttles everyone. The naive
    fix (left-most ``X-Forwarded-For``) is worse: that entry is fully
    client-supplied, so an attacker rotates a fake header and sidesteps every
    limit.

    Instead we trust exactly ``RATELIMIT_TRUSTED_PROXY_COUNT`` hops. Each trusted
    proxy appends the address it received from to the right of the header, so with
    ``N`` trusted proxies the real client is the ``N``-th entry counted from the
    right (``xff[-(N+1)]``). Entries to the left of that are attacker-controlled
    and ignored.

    Defaults to ``0`` (trust no proxy) so the header is never honoured unless the
    deployment opts in by declaring its hop count — a spoofed header then falls
    back to ``REMOTE_ADDR``, which the attacker cannot forge. Production sets the
    real count (client -> Traefik -> Next.js -> Django).
    """
    remote_addr = request.META.get("REMOTE_ADDR", "") or "unknown"
    trusted = int(getattr(settings, "RATELIMIT_TRUSTED_PROXY_COUNT", 0) or 0)
    if trusted <= 0:
        return remote_addr

    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    parts = [p.strip() for p in forwarded.split(",") if p.strip()]
    # The header must carry at least one entry per trusted hop plus the client
    # itself; a shorter chain means the expected proxies did not all append, so we
    # refuse to read an attacker-shiftable position and fall back to REMOTE_ADDR.
    if len(parts) <= trusted:
        return remote_addr
    return parts[-(trusted + 1)]


def client_ip_ratelimit_key(group, request) -> str:
    """Rate-limit key keyed on the trusted client IP (see resolve_client_ip)."""
    return resolve_client_ip(request)


def _parse_json_body(request):
    try:
        return json.loads(request.body), None
    except json.JSONDecodeError:
        return None, JsonResponse({"error": "Invalid JSON"}, status=400)


def _validate_message(body) -> tuple[str | None, JsonResponse | None]:
    message = body.get("message", "").strip()
    if not message:
        return None, JsonResponse({"error": "Message is required"}, status=400)
    if len(message) > MAX_MESSAGE_LENGTH:
        return None, JsonResponse({"error": "Message too long"}, status=400)
    return message, None


SUPPORTED_DOC_LANGUAGES = {"en", "ru"}


def _parse_document_language(body) -> tuple[str | None, JsonResponse | None]:
    """Pull `documentLanguage` from request body. Returns (language or None, error).

    None means the caller did not supply the field — keep existing behaviour.
    An empty/invalid value is a 400.
    """
    if not isinstance(body, dict) or "documentLanguage" not in body:
        return None, None
    raw = body.get("documentLanguage")
    if not isinstance(raw, str):
        return None, JsonResponse(
            {"error": "documentLanguage must be 'en' or 'ru'"}, status=400
        )
    normalised = raw.lower()
    if normalised not in SUPPORTED_DOC_LANGUAGES:
        return None, JsonResponse(
            {"error": "documentLanguage must be 'en' or 'ru'"}, status=400
        )
    return normalised, None


def _parse_attachment_ids(body) -> list[str]:
    ids = body.get("attachmentIds") or []
    if not isinstance(ids, list):
        return []
    return [str(x) for x in ids if x]


def _parse_document_html(body) -> str | None:
    """Read the editor's live document HTML from a chat body.

    The brief editor posts ``documentHtml`` with every message so the AI edits
    on top of the client's in-flight manual changes. Returns None when absent or
    not a string; capped at the same limit as the document PATCH endpoint.
    """
    html = body.get("documentHtml")
    if not isinstance(html, str):
        return None
    return html[:MAX_FINAL_DOCUMENT_HTML_LENGTH]


def _get_brief_for_client(brief_id, request) -> Brief | None:
    client_id = request.user_data.get("client_id")
    if not client_id:
        return None
    return Brief.objects.filter(
        id=brief_id,
        client_id=client_id,
        deleted_at__isnull=True,
    ).first()


def _get_brief_for_token(brief_id, request) -> Brief | None:
    token = request.headers.get("X-Brief-Token", "")
    if not token:
        return None
    return Brief.objects.filter(
        id=brief_id,
        anonymous_token=token,
        deleted_at__isnull=True,
    ).first()


def _get_client_safe(request) -> Client | None:
    client_id = request.user_data.get("client_id")
    if not client_id:
        return None
    return Client.objects.filter(id=client_id).first()


def _ensure_client_for_claim(request) -> Client | None:
    """Lazily create the Client profile for the authenticated user on claim.

    A lead who registered through a personal-link email needs a Client profile
    to own the brief, but we must not flip their User.group — the role toggle is
    a separate explicit action (PRD S2-14, §12 p.3/15).
    """
    user = _get_request_user(request)
    if not user:
        return None
    client, _created = Client.objects.get_or_create(
        owner=user,
        defaults={"name": f"{user.name}'s Company", "ein": ""},
    )
    return client


def _get_request_user(request) -> User | None:
    user_id = (getattr(request, "user_data", None) or {}).get("id")
    if not user_id:
        return None
    return User.objects.filter(id=user_id).first()


def _build_chat_response(
    brief: Brief,
    assistant_message: ChatMessage,
    result: dict,
) -> dict:
    updated = result.get("updated_documents") or []
    return {
        "reply": result["reply"],
        "messageId": str(assistant_message.id),
        "readyToFinalize": result["ready_to_finalize"],
        "conversationStatus": brief.conversation_status,
        "documentLanguage": brief.document_language,
        "inputTokens": result["input_tokens"],
        "outputTokens": result["output_tokens"],
        "costUsd": str(Decimal(str(result["cost_usd"]))),
        "messageCount": brief.message_count,
        "updatedDocuments": [serialize_brief_final_document(x) for x in updated],
    }


def _handle_post_finalize_feedback(
    brief: Brief,
    user_message_obj: ChatMessage,
) -> tuple[ChatMessage, dict, None] | None:
    """If the assistant just asked a feedback question, treat this user turn as
    a feedback submission: store the comment + a thanks-ack message and skip
    LLM completely. Returns None when the branch does not apply.
    """
    if brief.conversation_status != "finalized":
        return None

    last_assistant = (
        brief.chat_messages.filter(role="assistant")
        .exclude(id=user_message_obj.id)
        .order_by("-created_at")
        .first()
    )
    if not last_assistant or last_assistant.kind != ChatMessage.KIND_FEEDBACK_REQUEST:
        return None

    already_answered = brief.chat_messages.filter(
        kind=ChatMessage.KIND_FEEDBACK_REPLY_ACK
    ).exists()
    if already_answered:
        return None

    try:
        BriefFeedback.objects.create(
            brief=brief,
            message=user_message_obj,
            rating="up",
            comment=user_message_obj.content[:MAX_FEEDBACK_COMMENT_LENGTH],
            user=user_message_obj.user,
        )
    except Exception:
        logger.exception(
            "feedback save failed brief_id=%s user_msg=%s",
            brief.id,
            user_message_obj.id,
        )

    ack_text = feedback_ack_for(brief.document_language)
    ack = ChatMessage.objects.create(
        brief=brief,
        user=None,
        role="assistant",
        kind=ChatMessage.KIND_FEEDBACK_REPLY_ACK,
        content=ack_text,
    )
    result = {
        "reply": ack_text,
        "ready_to_finalize": False,
        "conversation_status": brief.conversation_status,
        "document_language": brief.document_language,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0,
        "model_used": "",
        "traces": [],
    }
    return ack, result, None


def _process_chat(
    brief: Brief,
    user_message_obj: ChatMessage,
    message_text: str,
    current_document_html: str | None = None,
) -> tuple[ChatMessage | None, dict | None, JsonResponse | None]:
    # Post-finalize feedback branch: if the last assistant message was a
    # feedback_request, treat the user reply as feedback text and short-circuit
    # without hitting the LLM. Store the feedback and acknowledge back.
    feedback_branch = _handle_post_finalize_feedback(brief, user_message_obj)
    if feedback_branch is not None:
        return feedback_branch

    history = list(
        brief.chat_messages.exclude(id=user_message_obj.id)
        .prefetch_related("attachments")
        .order_by("created_at")
    )
    attachments = list(
        BriefAttachment.objects.filter(brief=brief, message=user_message_obj).order_by(
            "created_at"
        )
    )

    is_finalized = brief.conversation_status == "finalized"
    turn_runner = process_finalized_turn if is_finalized else process_brief_turn
    # documentHtml is only meaningful once the editable document exists (finalized
    # turn); pre-finalize chat has no document yet.
    extra_kwargs: dict[str, Any] = {}
    if is_finalized and current_document_html is not None:
        extra_kwargs["current_document_html"] = current_document_html

    try:
        result = turn_runner(
            brief=brief,
            user_message=message_text,
            attachments=attachments,
            history=history,
            **extra_kwargs,
        )
    except Exception:
        logger.exception(
            "chat turn failed: brief_id=%s runner=%s",
            brief.id,
            turn_runner.__name__,
        )
        return (
            None,
            None,
            JsonResponse({"error": "Brief chat failed"}, status=500),
        )

    with transaction.atomic():
        updates = {
            "total_input_tokens": F("total_input_tokens") + result["input_tokens"],
            "total_output_tokens": F("total_output_tokens") + result["output_tokens"],
            "total_cost_usd": F("total_cost_usd") + Decimal(str(result["cost_usd"])),
            "message_count": F("message_count") + 1,
        }
        if brief.conversation_status != "finalized":
            updates["conversation_status"] = result["conversation_status"]
        if not brief.document_language and result.get("document_language"):
            updates["document_language"] = result["document_language"]

        Brief.objects.filter(id=brief.id).update(**updates)

        assistant_message = ChatMessage.objects.create(
            brief=brief,
            user=None,
            role="assistant",
            content=result["reply"],
            input_tokens=result["input_tokens"],
            output_tokens=result["output_tokens"],
            cost_usd=Decimal(str(result["cost_usd"])),
            model_used=result["model_used"],
            ready_to_finalize=result["ready_to_finalize"],
        )
        persist_message_traces(assistant_message, result.get("traces", []))

    brief.refresh_from_db()
    return assistant_message, result, None


def _validate_attachment_file(
    request, limit_count: int, existing_count: int
) -> tuple[UploadedFile | None, JsonResponse | None]:
    if existing_count >= limit_count:
        return None, JsonResponse({"error": "Attachment limit reached"}, status=429)

    uploaded = request.FILES.get("file")
    if not uploaded:
        return None, JsonResponse({"error": "file is required"}, status=400)

    if uploaded.size > MAX_ATTACHMENT_SIZE_BYTES:
        return None, JsonResponse({"error": "File too large"}, status=400)

    declared_mime = (uploaded.content_type or "").lower()
    if declared_mime not in ALLOWED_MIME_TYPES:
        return None, JsonResponse(
            {"error": f"Unsupported file type: {declared_mime}"}, status=400
        )

    detected_mime = sniff_mime(uploaded).lower()
    docx_ok = declared_mime == DOCX_MIME and detected_mime in DOCX_DETECTED_ALIASES
    if detected_mime and detected_mime not in ALLOWED_MIME_TYPES and not docx_ok:
        logger.warning(
            "Attachment MIME mismatch: declared=%s detected=%s",
            declared_mime,
            detected_mime,
        )
        return None, JsonResponse(
            {"error": f"File content does not match type: {detected_mime}"},
            status=400,
        )

    return uploaded, None


def _validate_audio_upload(
    request,
) -> tuple[UploadedFile | None, str | None, JsonResponse | None]:
    uploaded = request.FILES.get("audio")
    if not uploaded:
        return None, None, JsonResponse({"error": "audio is required"}, status=400)

    if uploaded.size > stt.MAX_AUDIO_BYTES:
        return (
            None,
            None,
            JsonResponse(
                {"error": "Audio too large", "code": stt.ERROR_AUDIO_TOO_LARGE},
                status=400,
            ),
        )

    declared_mime = (uploaded.content_type or "").lower()
    base_declared = declared_mime.split(";")[0].strip()
    if (
        declared_mime not in stt.ALLOWED_AUDIO_MIMES
        and base_declared not in stt.ALLOWED_AUDIO_MIMES
    ):
        return (
            None,
            None,
            JsonResponse(
                {
                    "error": f"Unsupported audio type: {declared_mime}",
                    "code": stt.ERROR_UNSUPPORTED_FORMAT,
                },
                status=400,
            ),
        )

    detected_mime = sniff_mime(uploaded).lower()
    base_detected = detected_mime.split(";")[0].strip()
    if (
        detected_mime
        and detected_mime not in stt.ALLOWED_AUDIO_MIMES
        and base_detected not in stt.ALLOWED_AUDIO_MIMES
        and base_detected not in stt.AUDIO_SNIFF_VIDEO_ALIASES
    ):
        logger.warning(
            "Audio MIME mismatch: declared=%s detected=%s",
            declared_mime,
            detected_mime,
        )
        return (
            None,
            None,
            JsonResponse(
                {
                    "error": f"Audio content does not match type: {detected_mime}",
                    "code": stt.ERROR_UNSUPPORTED_FORMAT,
                },
                status=400,
            ),
        )

    return uploaded, declared_mime, None


_TRANSCRIPTION_STATUS_MAP = {
    stt.ERROR_UNSUPPORTED_FORMAT: 400,
    stt.ERROR_AUDIO_TOO_LARGE: 400,
    stt.ERROR_AUDIO_TOO_LONG: 400,
    stt.ERROR_NO_SPEECH: 422,
    stt.ERROR_QUOTA_EXCEEDED: 503,
    stt.ERROR_INTERNAL: 500,
}


def _resolve_transcribe_language(request, brief: Brief) -> str:
    raw = (request.POST.get("language") or "").strip().lower()
    if raw in SUPPORTED_DOC_LANGUAGES:
        return raw
    return brief.document_language or "en"


def _parse_client_duration_ms(request) -> int:
    raw = (request.POST.get("durationMs") or "").strip()
    if not raw:
        return 0
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def _transcription_error_response(error: stt.TranscriptionError) -> JsonResponse:
    status = _TRANSCRIPTION_STATUS_MAP.get(error.code, 500)
    return JsonResponse({"error": str(error), "code": error.code}, status=status)


# ---------------------------------------------------------------------------
# Client endpoints
# ---------------------------------------------------------------------------


@csrf_exempt
@require_http_methods(["GET"])
@require_groups("CLIENT")
def client_brief_ai_list(request):
    client_id = request.user_data.get("client_id")
    if not client_id:
        return JsonResponse([], safe=False)

    briefs = (
        Brief.objects.filter(client_id=client_id, deleted_at__isnull=True)
        .prefetch_related("brief_offers")
        .order_by("-created_at")
    )
    return JsonResponse([serialize_brief_v3_list_item(b) for b in briefs], safe=False)


@csrf_exempt
@require_http_methods(["GET"])
@require_groups("CLIENT")
def client_brief_ai_sent_to_vendor(request):
    """List the client's briefs already sent to the vendor behind ``slug``.

    A brief counts as sent once it has a project for that vendor at RFP or
    beyond. The frontend uses this to hide the Send button for briefs already
    delivered to the vendor on the branded page.
    """
    client_id = request.user_data.get("client_id")
    slug = (request.GET.get("slug") or "").strip()
    if not client_id or not slug:
        return JsonResponse({"briefIds": []})

    vendor = _resolve_vendor_by_slug(slug)
    if not vendor:
        return JsonResponse({"briefIds": []})

    brief_ids = (
        Project.objects.filter(
            vendor=vendor,
            brief__client_id=client_id,
            brief__deleted_at__isnull=True,
            status__in=SENT_PROJECT_STATUSES,
        )
        .values_list("brief_id", flat=True)
        .distinct()
    )
    return JsonResponse({"briefIds": [str(x) for x in brief_ids]})


@csrf_exempt
@require_http_methods(["POST"])
@require_groups("CLIENT")
@conditional_ratelimit(key="user", rate="20/m", method="POST")
def client_brief_ai_drafts(request):
    """Create an empty draft brief so the user can upload attachments before
    sending the first message."""
    client = _get_client_safe(request)
    if not client:
        return JsonResponse({"error": "Client profile not found"}, status=403)

    user = User.objects.filter(id=request.user_data["id"]).first()
    if not user:
        return JsonResponse({"error": "User not found"}, status=401)

    brief = Brief.objects.create(client=client)
    return JsonResponse({"briefId": str(brief.id)}, status=201)


@csrf_exempt
@require_http_methods(["POST"])
@require_groups("CLIENT")
@conditional_ratelimit(key="user", rate="20/m", method="POST")
def client_brief_ai_start(request, brief_id):
    brief = _get_brief_for_client(brief_id, request)
    if not brief:
        return JsonResponse({"error": "Brief not found"}, status=404)

    if brief.message_count > 0:
        return JsonResponse({"error": "Brief already started"}, status=409)

    body, error = _parse_json_body(request)
    if error:
        return error

    message, error = _validate_message(body)
    if error or message is None:
        return error or JsonResponse({"error": "Message is required"}, status=400)

    document_language, error = _parse_document_language(body)
    if error:
        return error

    user = User.objects.filter(id=request.user_data["id"]).first()
    if not user:
        return JsonResponse({"error": "User not found"}, status=401)

    attachment_ids = _parse_attachment_ids(body)

    brief_id_str = str(brief.id)
    task_id = str(uuid.uuid4())
    with transaction.atomic():
        user_message = ChatMessage.objects.create(
            brief=brief,
            user=user,
            role="user",
            content=message,
        )
        update_kwargs: dict[str, Any] = {
            "message_count": F("message_count") + 1,
            "pending_task_id": task_id,
        }
        if document_language:
            update_kwargs["document_language"] = document_language
        Brief.objects.filter(id=brief.id).update(**update_kwargs)

        if attachment_ids:
            BriefAttachment.objects.filter(
                id__in=attachment_ids,
                brief=brief,
                message__isnull=True,
            ).update(message=user_message)

    transaction.on_commit(
        lambda: generate_first_reply_task.apply_async(
            args=[brief_id_str],
            task_id=task_id,
            link_error=clear_brief_pending_task.si(brief_id_str),
        )
    )
    return JsonResponse(
        {"briefId": brief_id_str, "taskId": task_id},
        status=201,
    )


@csrf_exempt
@require_http_methods(["GET"])
@require_groups("CLIENT")
def client_brief_ai_status(request, brief_id):
    brief = _get_brief_for_client(brief_id, request)
    if not brief:
        return JsonResponse({"error": "Brief not found"}, status=404)

    brief.refresh_from_db()
    if brief.pending_task_error:
        # The Send chain failed: its link_error stamped pending_task_error and
        # cleared the pending marker. Report "failed" (never "done") and clear
        # the flag so a fresh Send can re-arm cleanly.
        Brief.objects.filter(id=brief.id).update(pending_task_error="")
        logger.error("Send chain failed: brief_id=%s", brief_id)
        return JsonResponse({"status": "failed", "error": "Send failed"})
    if brief.pending_task_id:
        result = AsyncResult(brief.pending_task_id)
        if result.failed():
            logger.error(
                "Generation task failed: brief_id=%s error=%s",
                brief_id,
                result.result,
            )
            return JsonResponse({"status": "failed", "error": "Generation failed"})
        return JsonResponse({"status": "pending"})

    return JsonResponse(
        {
            "status": "done",
            "result": serialize_brief_v3_detail(brief, user=_get_request_user(request)),
        }
    )


@csrf_exempt
@require_http_methods(["POST"])
@require_groups("CLIENT")
@conditional_ratelimit(key="user", rate="20/m", method="POST")
def client_brief_ai_chat(request, brief_id):
    brief = _get_brief_for_client(brief_id, request)
    if not brief:
        return JsonResponse({"error": "Brief not found"}, status=404)

    if MESSAGE_LIMIT_AUTH and brief.message_count >= MESSAGE_LIMIT_AUTH:
        return JsonResponse({"error": "Message limit reached"}, status=429)

    if brief.total_cost_usd >= MAX_BRIEF_COST_USD:
        return JsonResponse(
            {
                "error": "Brief AI cost limit reached",
                "code": "cost_limit_reached",
                "limitUsd": str(MAX_BRIEF_COST_USD),
            },
            status=429,
        )

    body, error = _parse_json_body(request)
    if error:
        return error

    message, error = _validate_message(body)
    if error or message is None:
        return error or JsonResponse({"error": "Message is required"}, status=400)

    user = User.objects.filter(id=request.user_data["id"]).first()
    if not user:
        return JsonResponse({"error": "User not found"}, status=401)

    attachment_ids = _parse_attachment_ids(body)
    document_html = _parse_document_html(body)

    with transaction.atomic():
        user_message = ChatMessage.objects.create(
            brief=brief,
            user=user,
            role="user",
            content=message,
        )
        Brief.objects.filter(id=brief.id).update(message_count=F("message_count") + 1)
        if attachment_ids:
            BriefAttachment.objects.filter(
                id__in=attachment_ids,
                brief=brief,
                message__isnull=True,
            ).update(message=user_message)

    brief.refresh_from_db()
    assistant_message, result, error_response = _process_chat(
        brief, user_message, message, current_document_html=document_html
    )
    if error_response or assistant_message is None or result is None:
        return error_response or JsonResponse(
            {"error": "Brief chat failed"}, status=500
        )

    brief.refresh_from_db()
    return JsonResponse(_build_chat_response(brief, assistant_message, result))


@csrf_exempt
@require_http_methods(["GET", "PATCH", "DELETE"])
@require_groups("CLIENT")
def client_brief_ai_detail(request, brief_id):
    brief = _get_brief_for_client(brief_id, request)
    if not brief:
        return JsonResponse({"error": "Brief not found"}, status=404)

    if request.method == "GET":
        return JsonResponse(
            serialize_brief_v3_detail(brief, user=_get_request_user(request))
        )

    if request.method == "DELETE":
        brief.deleted_at = timezone.now()
        brief.save(update_fields=["deleted_at", "updated_at"])
        return JsonResponse({"deleted": True}, status=200)

    body, error = _parse_json_body(request)
    if error:
        return error

    update_fields: list[str] = []

    if "title" in body:
        title = body.get("title")
        if not isinstance(title, str) or not title.strip():
            return JsonResponse({"error": "title must be non-empty"}, status=400)
        brief.title = title.strip()[:255]
        update_fields.append("title")

    if "documentLanguage" in body:
        language = body.get("documentLanguage")
        if not isinstance(language, str) or language.lower() not in {"en", "ru"}:
            return JsonResponse(
                {"error": "documentLanguage must be 'en' or 'ru'"}, status=400
            )
        brief.document_language = language.lower()
        update_fields.append("document_language")

    if not update_fields:
        return JsonResponse(
            {"error": "title or documentLanguage is required"}, status=400
        )

    update_fields.append("updated_at")
    brief.save(update_fields=update_fields)
    return JsonResponse(serialize_brief_v3_list_item(brief))


@csrf_exempt
@require_http_methods(["POST"])
@require_groups("CLIENT")
@conditional_ratelimit(key="user", rate="30/m", method="POST")
def client_brief_ai_attachments(request, brief_id):
    brief = _get_brief_for_client(brief_id, request)
    if not brief:
        return JsonResponse({"error": "Brief not found"}, status=404)

    existing = brief.attachments.count()
    uploaded, error = _validate_attachment_file(
        request, MAX_ATTACHMENTS_PER_BRIEF_AUTH, existing
    )
    if error or uploaded is None:
        return error or JsonResponse({"error": "file is required"}, status=400)

    attachment = BriefAttachment.objects.create(
        brief=brief,
        file=uploaded,
        filename=(uploaded.name or "")[:255],
        mime_type=uploaded.content_type or "application/octet-stream",
        size_bytes=uploaded.size or 0,
    )
    return JsonResponse(serialize_brief_attachment(attachment), status=201)


@csrf_exempt
@require_http_methods(["DELETE"])
@require_groups("CLIENT")
def client_brief_ai_attachment_delete(request, brief_id, attachment_id):
    brief = _get_brief_for_client(brief_id, request)
    if not brief:
        return JsonResponse({"error": "Brief not found"}, status=404)

    attachment = BriefAttachment.objects.filter(id=attachment_id, brief=brief).first()
    if not attachment:
        return JsonResponse({"error": "Attachment not found"}, status=404)

    if attachment.message_id is not None:
        return JsonResponse(
            {"error": "Attachment is already sent, cannot delete"}, status=409
        )

    try:
        attachment.file.delete(save=False)
    except Exception:
        logger.exception("Failed to delete attachment file %s", attachment.id)
    attachment.delete()
    return JsonResponse({"deleted": True})


@csrf_exempt
@require_http_methods(["POST"])
@require_groups("CLIENT")
@conditional_ratelimit(key="user", rate="10/m", method="POST")
def client_brief_ai_chat_transcribe(request, brief_id):
    brief = _get_brief_for_client(brief_id, request)
    if not brief:
        return JsonResponse({"error": "Brief not found"}, status=404)

    if brief.total_cost_usd >= MAX_BRIEF_COST_USD:
        return JsonResponse(
            {
                "error": "Brief AI cost limit reached",
                "code": "cost_limit_reached",
                "limitUsd": str(MAX_BRIEF_COST_USD),
            },
            status=429,
        )

    uploaded, declared_mime, error = _validate_audio_upload(request)
    if error or uploaded is None or declared_mime is None:
        return error or JsonResponse({"error": "audio is required"}, status=400)

    audio_bytes = uploaded.read()
    language = _resolve_transcribe_language(request, brief)
    duration_ms = _parse_client_duration_ms(request)
    dynamic_hints, static_hints = stt.build_phrase_hints(brief, language)

    try:
        result = stt.transcribe_audio(
            audio_bytes,
            declared_mime,
            language,
            dynamic_hints,
            static_hints,
        )
    except stt.TranscriptionError as ex:
        logger.warning("STT auth failed: brief=%s code=%s", brief.id, ex.code)
        return _transcription_error_response(ex)

    logger.info(
        "STT auth ok: brief=%s size=%s duration_ms=%s lang=%s mime=%s "
        "chars=%s hints=%s/%s",
        brief.id,
        len(audio_bytes),
        duration_ms,
        language,
        declared_mime,
        len(result["text"]),
        len(dynamic_hints),
        len(static_hints),
    )
    return JsonResponse(
        {
            "text": result["text"],
            "language": result["language"],
            "model": result["model"],
        },
    )


@csrf_exempt
@require_http_methods(["POST"])
@require_groups("CLIENT")
def client_brief_ai_feedback(request, brief_id):
    brief = _get_brief_for_client(brief_id, request)
    if not brief:
        return JsonResponse({"error": "Brief not found"}, status=404)

    body, error = _parse_json_body(request)
    if error:
        return error

    user = User.objects.filter(id=request.user_data["id"]).first()
    if not user:
        return JsonResponse({"error": "User not found"}, status=401)

    rating = body.get("rating", "up")
    if rating not in VALID_FEEDBACK_RATINGS:
        return JsonResponse({"error": "Invalid rating"}, status=400)

    comment = body.get("comment", "")
    if len(comment) > MAX_FEEDBACK_COMMENT_LENGTH:
        return JsonResponse({"error": "Comment too long"}, status=400)

    message_id = body.get("messageId")
    if not message_id:
        return JsonResponse({"error": "messageId is required"}, status=400)

    chat_message = ChatMessage.objects.filter(id=message_id, brief=brief).first()
    if not chat_message:
        return JsonResponse({"error": "Message not found"}, status=404)

    feedback = BriefFeedback.objects.create(
        brief=brief,
        message=chat_message,
        rating=rating,
        comment=comment,
        user=user,
    )
    return JsonResponse(serialize_brief_feedback(feedback), status=201)


@csrf_exempt
@require_http_methods(["GET"])
@require_groups("CLIENT")
def client_brief_ai_message_trace(request, brief_id, message_id):
    user = User.objects.filter(id=request.user_data["id"]).first()
    if not user or not user.is_staff:
        return JsonResponse({"error": "Forbidden"}, status=403)

    brief = _get_brief_for_client(brief_id, request)
    if not brief:
        return JsonResponse({"error": "Brief not found"}, status=404)

    chat_message = ChatMessage.objects.filter(id=message_id, brief=brief).first()
    if not chat_message:
        return JsonResponse({"error": "Message not found"}, status=404)

    traces = list(chat_message.llm_traces.order_by("sequence", "created_at"))
    return JsonResponse(
        {
            "messageId": str(chat_message.id),
            "modelUsed": chat_message.model_used,
            "inputTokens": chat_message.input_tokens,
            "outputTokens": chat_message.output_tokens,
            "costUsd": str(chat_message.cost_usd),
            "createdAt": chat_message.created_at.isoformat()
            if chat_message.created_at
            else None,
            "traces": [
                {
                    "id": str(trace.id),
                    "sequence": trace.sequence,
                    "purpose": trace.purpose,
                    "model": trace.model,
                    "requestMessages": trace.request_messages,
                    "requestParams": trace.request_params,
                    "responseRaw": trace.response_raw,
                    "inputTokens": trace.input_tokens,
                    "outputTokens": trace.output_tokens,
                    "costUsd": str(trace.cost_usd),
                    "latencyMs": trace.latency_ms,
                    "createdAt": trace.created_at.isoformat()
                    if trace.created_at
                    else None,
                }
                for trace in traces
            ],
        }
    )


@csrf_exempt
@require_http_methods(["POST"])
@require_groups("CLIENT")
@conditional_ratelimit(key="user", rate="5/m", method="POST")
def client_brief_ai_finalize(request, brief_id):
    brief = _get_brief_for_client(brief_id, request)
    if not brief:
        return JsonResponse({"error": "Brief not found"}, status=404)

    if brief.total_cost_usd >= MAX_BRIEF_COST_USD:
        return JsonResponse(
            {
                "error": "Brief AI cost limit reached",
                "code": "cost_limit_reached",
                "limitUsd": str(MAX_BRIEF_COST_USD),
            },
            status=429,
        )

    try:
        body = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        body = {}
    if isinstance(body, dict):
        document_language, error = _parse_document_language(body)
        if error:
            return error
        if document_language and document_language != brief.document_language:
            brief.document_language = document_language
            brief.save(update_fields=["document_language", "updated_at"])

    brief_id_str = str(brief.id)
    task_id = str(uuid.uuid4())
    with transaction.atomic():
        Brief.objects.filter(id=brief.id).update(pending_task_id=task_id)
    transaction.on_commit(
        lambda: finalize_brief_task.apply_async(
            args=[brief_id_str],
            task_id=task_id,
            link_error=clear_brief_pending_task.si(brief_id_str),
        )
    )
    return JsonResponse({"taskId": task_id})


@csrf_exempt
@require_http_methods(["GET"])
@require_groups("CLIENT")
def client_brief_ai_final_documents(request, brief_id):
    brief = _get_brief_for_client(brief_id, request)
    if not brief:
        return JsonResponse({"error": "Brief not found"}, status=404)

    documents = brief.final_documents.order_by("kind")
    return JsonResponse(
        {
            "briefId": str(brief.id),
            "conversationStatus": brief.conversation_status,
            "documents": [serialize_brief_final_document(x) for x in documents],
        }
    )


@csrf_exempt
@require_http_methods(["PATCH"])
@require_groups("CLIENT")
@conditional_ratelimit(key="user", rate="60/m", method="PATCH")
def client_brief_ai_final_document_update(request, brief_id, document_id):
    brief = _get_brief_for_client(brief_id, request)
    if not brief:
        return JsonResponse({"error": "Brief not found"}, status=404)

    document = BriefFinalDocument.objects.filter(id=document_id, brief=brief).first()
    if not document:
        return JsonResponse({"error": "Document not found"}, status=404)

    body, error = _parse_json_body(request)
    if error:
        return error

    html = body.get("html")
    if not isinstance(html, str):
        return JsonResponse({"error": "html is required"}, status=400)
    if len(html) > MAX_FINAL_DOCUMENT_HTML_LENGTH:
        return JsonResponse({"error": "Document too large"}, status=400)

    document.html = sanitize_html(html)

    plain_text = body.get("plainText")
    if isinstance(plain_text, str):
        document.plain_text = plain_text[:MAX_FINAL_DOCUMENT_HTML_LENGTH]

    document.save(update_fields=["html", "plain_text", "updated_at"])
    return JsonResponse(serialize_brief_final_document(document))


@csrf_exempt
@require_http_methods(["GET"])
@require_groups("CLIENT")
def client_brief_ai_final_document_pdf(request, brief_id, document_id):
    brief = _get_brief_for_client(brief_id, request)
    if not brief:
        return JsonResponse({"error": "Brief not found"}, status=404)

    document = BriefFinalDocument.objects.filter(id=document_id, brief=brief).first()
    if not document:
        return JsonResponse({"error": "Document not found"}, status=404)
    return _pdf_response_for_document(document)


def _pdf_response_for_document(document: BriefFinalDocument) -> HttpResponse:
    from urllib.parse import quote  # noqa: PLC0415

    from aivus_backend.projects.brief_pdf import DOCUMENT_TITLE_BY_KIND  # noqa: PLC0415
    from aivus_backend.projects.brief_pdf import (  # noqa: PLC0415
        render_final_document_pdf,
    )

    pdf_bytes = render_final_document_pdf(document)
    label = DOCUMENT_TITLE_BY_KIND.get(document.kind, "Brief")
    base_name = (document.brief.title or "Brief").strip()
    safe = "".join(c for c in base_name if c.isalnum() or c in " _-").strip()[:60]
    filename = f"{safe or 'Brief'} - {label}.pdf"
    encoded = quote(filename)

    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = (
        f"attachment; filename=\"{filename}\"; filename*=UTF-8''{encoded}"
    )
    return response


# ---------------------------------------------------------------------------
# Share endpoints
# ---------------------------------------------------------------------------


@csrf_exempt
@require_http_methods(["GET", "POST", "PATCH"])
@require_groups("CLIENT")
@conditional_ratelimit(key="user", rate="30/m", method="POST")
def client_brief_ai_share(request, brief_id):
    brief = _get_brief_for_client(brief_id, request)
    if not brief:
        return JsonResponse({"error": "Brief not found"}, status=404)

    if request.method == "GET":
        share = BriefShare.objects.filter(brief=brief).first()
        if not share:
            return JsonResponse({"error": "No share yet"}, status=404)
        return JsonResponse(serialize_brief_share(share))

    if request.method == "POST":
        if brief.conversation_status != "finalized":
            return JsonResponse(
                {"error": "Brief must be finalized before sharing"}, status=400
            )
        user = _get_request_user(request)
        share, created = BriefShare.objects.get_or_create(
            brief=brief, defaults={"created_by": user}
        )
        return JsonResponse(
            serialize_brief_share(share), status=201 if created else 200
        )

    # PATCH — toggle active
    share = BriefShare.objects.filter(brief=brief).first()
    if not share:
        return JsonResponse({"error": "No share to update"}, status=404)

    body, error = _parse_json_body(request)
    if error:
        return error
    if "isActive" in body:
        share.is_active = bool(body["isActive"])
        share.save(update_fields=["is_active", "updated_at"])
    return JsonResponse(serialize_brief_share(share))


@csrf_exempt
@require_http_methods(["GET"])
@public_endpoint
@conditional_ratelimit(key=client_ip_ratelimit_key, rate="120/m", method="GET")
def public_brief_share_get(request, token):
    share = (
        BriefShare.objects.filter(token=token, is_active=True)
        .select_related("brief")
        .first()
    )
    if not share:
        return JsonResponse({"error": "Share not found or inactive"}, status=404)
    if share.brief.conversation_status != "finalized":
        return JsonResponse({"error": "Brief is not finalized yet"}, status=404)
    return JsonResponse(serialize_brief_share_public(share))


@csrf_exempt
@require_http_methods(["GET"])
@public_endpoint
@conditional_ratelimit(key=client_ip_ratelimit_key, rate="60/m", method="GET")
def public_brief_share_document_pdf(request, token, document_id):
    share = (
        BriefShare.objects.filter(token=token, is_active=True)
        .select_related("brief")
        .first()
    )
    if not share or share.brief.conversation_status != "finalized":
        return JsonResponse({"error": "Share not found"}, status=404)

    document = BriefFinalDocument.objects.filter(
        id=document_id, brief=share.brief
    ).first()
    if not document:
        return JsonResponse({"error": "Document not found"}, status=404)
    return _pdf_response_for_document(document)


# ---------------------------------------------------------------------------
# Public (anonymous) endpoints
# ---------------------------------------------------------------------------


def _wix_str(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _normalize_contact_email(value: str) -> str:
    return (value or "").strip().lower()[:254]


def _is_valid_email(value: str) -> bool:
    try:
        validate_email(value)
    except ValidationError:
        return False
    return True


def _normalize_contact_name(value: str) -> str:
    return (value or "").strip()[:255]


def _collect_wix_files(raw) -> list[dict]:
    if not isinstance(raw, list):
        return []
    files = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = _wix_str(item.get("url"))
        if not url:
            continue
        filename = (
            _wix_str(item.get("filename"))
            or _wix_str(item.get("displayName"))
            or "attachment"
        )
        files.append({"url": url, "filename": filename[:255]})
    return files


def _extract_wix_submission_text(submissions) -> str:
    if not isinstance(submissions, list):
        return ""
    best = ""
    for item in submissions:
        if not isinstance(item, dict):
            continue
        value = _wix_str(item.get("value"))
        if len(value) > len(best):
            best = value
    return best


def _extract_wix_payload(body: dict) -> dict:
    """Normalise both the compact Velo contract and the Wix Automation
    submission payload into {email, name, message, files}."""
    email = _wix_str(body.get("email"))
    name = _wix_str(body.get("name"))
    message = _wix_str(body.get("message"))
    files = _collect_wix_files(body.get("files"))

    contact = body.get("contact")
    if isinstance(contact, dict):
        if not email:
            email = _wix_str(contact.get("email"))
        if not name:
            contact_name = contact.get("name")
            if isinstance(contact_name, dict):
                name = _wix_str(contact_name.get("first")) or _wix_str(
                    contact_name.get("last")
                )

    if not message:
        message = _wix_str(body.get("field:long_answer"))
    if not message:
        message = _extract_wix_submission_text(body.get("submissions"))

    if not files:
        files = _collect_wix_files(body.get("field:initial_files"))

    return {"email": email, "name": name, "message": message, "files": files}


def _project_name_for_brief(brief: Brief) -> str:
    title = (brief.title or "").strip()
    return (title or "New brief lead")[:255]


SENDABLE_CONVERSATION_STATUSES = {"ready_to_finalize", "finalized"}
SENT_PROJECT_STATUSES = {
    ProjectStatus.RFP,
    ProjectStatus.REVIEWING,
    ProjectStatus.ONGOING,
}


def _brief_already_sent_to_vendor(brief: Brief, vendor: Vendor) -> bool:
    return (
        Project.objects.filter(brief=brief, vendor=vendor)
        .filter(status__in=SENT_PROJECT_STATUSES)
        .exists()
    )


def _brief_already_sent(brief: Brief) -> bool:
    """True once the brief has been sent to any vendor (project at RFP+).

    After Send the vendor reads the very same brief — there is no copy — so the
    anonymous client must not be able to keep editing the document the vendor is
    already looking at.
    """
    return (
        Project.objects.filter(brief=brief)
        .filter(status__in=SENT_PROJECT_STATUSES)
        .exists()
    )


def _dispatch_send(brief: Brief, vendor: Vendor, recipient_email: str, language: str):
    """Validate and enqueue the Send chain.

    Returns a JsonResponse. The chain finalizes the brief first when it has not
    been finalized yet, then promotes the vendor project to RFP and sends the
    emails. Every step is idempotent so retries and double-Send are safe.
    """
    if brief.conversation_status not in SENDABLE_CONVERSATION_STATUSES:
        return JsonResponse({"error": "Brief is not ready to send"}, status=400)

    brief_id_str = str(brief.id)
    vendor_id_str = str(vendor.id)
    # One pending id tracks the entire Send chain (finalize, if needed, then the
    # project promotion to RFP, then the emails). It is set before the chain runs
    # and cleared by the tail step, so the status endpoint reports "pending" until
    # the project has actually been promoted. Send never reports "sent" before the
    # promotion — the client polls this id and only then shows success.
    send_chain_task_id = str(uuid.uuid4())
    mark_signature = mark_project_sent_task.si(brief_id_str, vendor_id_str)
    send_signature = send_emails_task.si(
        brief_id_str, vendor_id_str, recipient_email, language
    )
    clear_signature = clear_brief_pending_task.si(brief_id_str)

    # Serialize concurrent Sends on the brief row so a double click or retry
    # cannot enqueue two chains (which would create a duplicate project and a
    # second set of emails). The locked re-read also catches a project that a
    # previous in-flight Send already promoted to RFP.
    with transaction.atomic():
        locked = Brief.objects.select_for_update().get(id=brief.id)
        if _brief_already_sent_to_vendor(locked, vendor):
            return JsonResponse(
                {"error": "Brief already sent to this vendor"}, status=409
            )

        # Only generate when no documents exist yet. The branded anonymous flow
        # renders the document on ready (finalize-on-ready) and lets the client
        # edit it before Send; re-running finalize here would discard those
        # manual edits. Documents already present are taken as-is.
        needs_finalize = not locked.final_documents.exists()
        # Any non-empty pending marker means a task already owns this brief, so a
        # second Send must not enqueue another chain. Two cases:
        #   1) needs_finalize — a GET-triggered finalize (the branded flow polls
        #      final-documents, which dispatches finalize-on-ready) is in flight
        #      with no documents yet. A second finalize would race the first and
        #      generate_final_documents would delete+recreate the document,
        #      discarding the in-flight result and any manual edits.
        #   2) not needs_finalize — a previous Send chain is already armed (its
        #      project promotion to RFP runs async, after this lock is released).
        #      A concurrent Send would see the project still at DRAFT, pass the
        #      _brief_already_sent_to_vendor guard, and enqueue a second chain that
        #      sends the client and vendor emails twice. The marker closes that gap.
        # Either way we leave the existing marker intact and tell the client to
        # retry, so the in-flight task is never disturbed.
        if locked.pending_task_id:
            message = (
                "Brief is still generating, try again shortly"
                if needs_finalize
                else "Brief is already being sent, try again shortly"
            )
            return JsonResponse({"error": message}, status=409)
        # Arm the pending marker and clear any stale failure from a previous Send
        # so the status endpoint reports "pending" again, not the old "failed".
        Brief.objects.filter(id=locked.id).update(
            pending_task_id=send_chain_task_id, pending_task_error=""
        )
        if needs_finalize:
            # finalize_brief_task clears pending_task_id when it completes, so the
            # marker is re-asserted right after it to keep the brief "pending"
            # until the project is actually promoted.
            workflow = chain(
                finalize_brief_task.si(brief_id_str),
                set_brief_pending_task.si(brief_id_str, send_chain_task_id),
                mark_signature,
                send_signature,
                clear_signature,
            )
        else:
            workflow = chain(mark_signature, send_signature, clear_signature)

    # On any step failure the chain's link_error stamps pending_task_error with
    # this chain id and clears the pending marker, so the status endpoint reports
    # "failed" instead of silently flipping to "done". Success runs
    # clear_brief_pending_task (the tail) which leaves pending_task_error empty.
    transaction.on_commit(
        lambda: workflow.apply_async(
            link_error=mark_brief_send_failed_task.si(brief_id_str, send_chain_task_id)
        )
    )

    response: dict[str, Any] = {"ok": True, "finalizingTaskId": send_chain_task_id}
    return JsonResponse(response)


def _resolve_vendor_by_slug(slug: str) -> Vendor | None:
    """Resolve an active vendor from a brief-link slug.

    The incoming slug is normalised to lowercase before the lookup so a
    MixedCase link still resolves; stored slugs are always lowercase. Soft-deleted
    vendors are filtered manually because the FK uses no on_delete cascade for
    deleted_at; a slug pointing at a deleted vendor must 404.
    """
    normalized = normalize_slug(slug)
    if not normalized:
        return None
    settings_row = (
        VendorSettings.objects.filter(slug=normalized).select_related("vendor").first()
    )
    if not settings_row:
        return None
    vendor = settings_row.vendor
    if vendor.deleted_at is not None:
        return None
    return vendor


def _public_send_vendor_matches_brief(brief: Brief, vendor: Vendor) -> bool:
    """Guard the anonymous Send against slug swapping.

    A personal-link brief is started on one vendor's branded page, which attaches
    exactly one DRAFT project to that vendor. The slug in the Send body must point
    back at that same vendor; otherwise an anonymous client could swap the slug
    and send the lead to the wrong agency. When the brief has no project yet
    (started outside the by-slug flow) the resolved vendor is accepted as-is.
    """
    existing_vendor_ids = set(
        Project.objects.filter(brief=brief, deleted_at__isnull=True)
        .values_list("vendor_id", flat=True)
        .distinct()
    )
    if not existing_vendor_ids:
        return True
    return vendor.id in existing_vendor_ids


def _vendor_public_branding(vendor: Vendor, slug: str) -> dict:
    settings_row = VendorSettings.objects.filter(vendor=vendor).first()
    logo_url = None
    vendor_name = vendor.name
    if settings_row:
        if settings_row.logo:
            logo_url = settings_row.logo.url
        vendor_name = (
            (settings_row.company_name or "").strip()
            or (settings_row.agency_name or "").strip()
            or vendor.name
        )
    return {
        "valid": True,
        "vendorName": vendor_name,
        "vendorLogoUrl": logo_url,
        "slug": slug,
    }


def _verify_wix_secret(request) -> bool:
    expected = getattr(settings, "WIX_WEBHOOK_SECRET", "")
    if not expected:
        return False
    provided = request.headers.get("X-Aivus-Webhook-Secret", "")
    return bool(provided) and hmac.compare_digest(provided, expected)


def _verify_vendor_webhook_key(request) -> Vendor | None:
    """Resolve the vendor from the per-vendor webhook key header.

    Uses a constant-time compare against the active key. A revoked or unknown
    key, or a soft-deleted vendor, returns None so the caller answers 401.
    """
    from aivus_backend.users.models import VendorWebhookKey  # noqa: PLC0415

    provided = request.headers.get("X-Aivus-Webhook-Key", "")
    if not provided:
        return None

    row = (
        VendorWebhookKey.objects.filter(is_active=True)
        .select_related("vendor")
        .filter(key=provided)
        .first()
    )
    if not row:
        return None
    if not hmac.compare_digest(row.key, provided):
        return None
    if row.vendor.deleted_at is not None:
        return None
    return row.vendor


def _create_inbound_brief(  # noqa: PLR0913
    *,
    message: str,
    contact_email: str = "",
    contact_name: str = "",
    file_specs: list[dict] | None = None,
    source: str = "",
    vendor=None,
) -> tuple[Brief, str, str]:
    file_specs = file_specs or []
    token = secrets.token_urlsafe(48)
    task_id = str(uuid.uuid4())

    with transaction.atomic():
        brief = Brief.objects.create(
            client=None,
            anonymous_token=token,
            contact_email=_normalize_contact_email(contact_email),
            contact_name=_normalize_contact_name(contact_name),
            source=source or BriefSource.DIRECT,
        )
        ChatMessage.objects.create(
            brief=brief,
            user=None,
            anonymous_token=token,
            role="user",
            content=message,
        )
        Brief.objects.filter(id=brief.id).update(
            message_count=F("message_count") + 1,
            pending_task_id=task_id,
        )
        if vendor is not None:
            Project.objects.get_or_create(
                vendor=vendor,
                brief=brief,
                defaults={
                    "name": _project_name_for_brief(brief),
                    "status": ProjectStatus.RFP,
                },
            )

    brief_id_str = str(brief.id)
    if file_specs:
        signature = chain(
            import_wix_attachments_task.s(brief_id_str, file_specs),
            generate_first_reply_task.si(brief_id_str).set(task_id=task_id),
        )
    else:
        signature = generate_first_reply_task.signature(
            args=(brief_id_str,), task_id=task_id
        )

    # ATOMIC_REQUESTS wraps the whole view in a transaction; enqueueing on_commit
    # guarantees the worker only sees the brief after pending_task_id is committed,
    # so there is no reverse race against the status endpoint.
    transaction.on_commit(
        lambda: signature.apply_async(
            link_error=clear_brief_pending_task.si(brief_id_str)
        )
    )
    return brief, task_id, token


@csrf_exempt
@require_http_methods(["POST"])
@public_endpoint
@conditional_ratelimit(key=client_ip_ratelimit_key, rate="30/h", method="POST")
def public_brief_ai_from_wix(request):
    """Create an anonymous brief from a Wix landing form submission, remember the
    contact email and start the first AI reply. Returns the public brief URL for
    Wix to redirect the visitor to."""
    if not _verify_wix_secret(request):
        return JsonResponse({"error": "Unauthorized"}, status=401)

    body, error = _parse_json_body(request)
    if error:
        return error
    if not isinstance(body, dict):
        return JsonResponse({"error": "Invalid payload"}, status=400)

    payload = _extract_wix_payload(body)

    message, error = _validate_message(payload)
    if error or message is None:
        return error or JsonResponse({"error": "Message is required"}, status=400)

    brief, task_id, token = _create_inbound_brief(
        message=message,
        contact_email=payload["email"],
        contact_name=payload["name"],
        file_specs=payload["files"][:MAX_ATTACHMENTS_PER_BRIEF_ANON],
        source="wix",
    )

    base_url = getattr(settings, "FRONTEND_URL", "https://go.aivus.co").rstrip("/")
    brief_url = f"{base_url}/public-brief/{brief.id}?token={token}&taskId={task_id}"
    return JsonResponse(
        {
            "briefId": str(brief.id),
            "token": token,
            "taskId": task_id,
            "briefUrl": brief_url,
        },
        status=201,
    )


@csrf_exempt
@require_http_methods(["POST"])
@public_endpoint
@conditional_ratelimit(key=client_ip_ratelimit_key, rate="30/h", method="POST")
def public_brief_ai_from_webhook(request):
    """Create a vendor lead via the per-vendor webhook key.

    Authenticates with the X-Aivus-Webhook-Key header (separate from the global
    Wix secret), creates the inbound brief with source=webhook and immediately
    attaches an RFP project to the vendor. An IP rate-limit fires before the key
    is resolved so an attacker cannot brute-force keys; once the key resolves a
    second 50/h per vendor_id limit caps a leaked key from flooding the inbox.
    """
    vendor = _verify_vendor_webhook_key(request)
    if not vendor:
        return JsonResponse({"error": "Unauthorized"}, status=401)

    if _webhook_vendor_ratelimited(request, vendor):
        return JsonResponse({"error": "Rate limit exceeded"}, status=429)

    body, error = _parse_json_body(request)
    if error:
        return error
    if not isinstance(body, dict):
        return JsonResponse({"error": "Invalid payload"}, status=400)

    payload = _extract_wix_payload(body)

    message, error = _validate_message(payload)
    if error or message is None:
        return error or JsonResponse({"error": "Message is required"}, status=400)

    brief, task_id, token = _create_inbound_brief(
        message=message,
        contact_email=payload["email"],
        contact_name=payload["name"],
        file_specs=payload["files"][:MAX_ATTACHMENTS_PER_BRIEF_ANON],
        source="webhook",
        vendor=vendor,
    )
    _notify_vendor_of_lead(brief, vendor, request)
    return JsonResponse(
        {"briefId": str(brief.id), "token": token, "taskId": task_id},
        status=201,
    )


def _notify_vendor_of_lead(brief: Brief, vendor: Vendor, request) -> None:
    """Dispatch the vendor lead notification for an inbound webhook brief.

    Mirrors the Send flow's vendor email, scheduled on_commit so the worker only
    runs once the brief and its project are persisted. The email language comes
    from the vendor's own settings (resolved inside send_vendor_lead_email), not
    the brief's document_language, which is empty for inbound webhook/wix leads.
    """
    from aivus_backend.projects import brief_emails  # noqa: PLC0415

    def _dispatch() -> None:
        project = Project.objects.filter(brief=brief, vendor=vendor).first()
        if not project:
            return
        brief_emails.send_vendor_lead_email(project, brief)

    transaction.on_commit(_dispatch)


def _webhook_vendor_ratelimited(request, vendor) -> bool:
    if not getattr(settings, "RATELIMIT_ENABLE", True):
        return False
    from django_ratelimit.core import is_ratelimited  # noqa: PLC0415

    return is_ratelimited(
        request=request,
        group="vendor_webhook",
        key=lambda *_args: str(vendor.id),
        rate="50/h",
        method="POST",
        increment=True,
    )


@csrf_exempt
@require_http_methods(["GET"])
@public_endpoint
@conditional_ratelimit(key=client_ip_ratelimit_key, rate="60/h", method="GET")
def public_brief_ai_by_slug(request, slug):
    """Resolve a vendor brief-link slug to public branding for the start screen.

    Returns 404 for unknown or soft-deleted vendors so the frontend can render
    the "link not found" state without leaking vendor existence detail.
    """
    vendor = _resolve_vendor_by_slug(slug)
    if not vendor:
        return JsonResponse({"valid": False, "error": "Link not found"}, status=404)
    return JsonResponse(_vendor_public_branding(vendor, slug))


@csrf_exempt
@require_http_methods(["POST"])
@public_endpoint
@conditional_ratelimit(key=client_ip_ratelimit_key, rate="30/h", method="POST")
def public_brief_ai_by_slug_drafts(request, slug):
    """Start an anonymous brief on a vendor's personal link.

    Creates the draft brief (source=personal_link) and immediately attaches a
    DRAFT project to the vendor so the lead shows up "in progress" before the
    client finishes. The project is created in the bypass path because the
    standard vendor guard forbids attaching anonymous briefs vendors did not
    already touch — here the vendor explicitly owns the link.
    """
    vendor = _resolve_vendor_by_slug(slug)
    if not vendor:
        return JsonResponse({"error": "Link not found"}, status=404)

    token = secrets.token_urlsafe(48)
    with transaction.atomic():
        brief = Brief.objects.create(
            client=None,
            anonymous_token=token,
            source=BriefSource.PERSONAL_LINK,
        )
        Project.objects.get_or_create(
            vendor=vendor,
            brief=brief,
            defaults={
                "name": _project_name_for_brief(brief),
                "status": ProjectStatus.DRAFT,
            },
        )
    return JsonResponse(
        {"briefId": str(brief.id), "token": token},
        status=201,
    )


@csrf_exempt
@require_http_methods(["POST"])
@public_endpoint
@conditional_ratelimit(key=client_ip_ratelimit_key, rate="10/h", method="POST")
def public_brief_ai_send(request, brief_id):
    """Anonymous Send: finalize (if needed), attach the vendor project, email.

    The vendor is resolved from the ``slug`` in the body; the contact email is
    required so the client receives their copy. The response shape is uniform
    regardless of whether the email already has an account (anti-enumeration).
    """
    from aivus_backend.projects import brief_emails  # noqa: PLC0415

    brief = _get_brief_for_token(brief_id, request)
    if not brief:
        return JsonResponse({"error": "Brief not found"}, status=404)

    body, error = _parse_json_body(request)
    if error:
        return error

    slug = (body.get("slug") or "").strip()
    vendor = _resolve_vendor_by_slug(slug) if slug else None
    if not vendor:
        return JsonResponse(
            {"error": "This agency is no longer accepting briefs"}, status=404
        )

    # The brief was started on one vendor's branded link; reject a Send whose slug
    # points at a different vendor than the brief's existing project (slug swap).
    if not _public_send_vendor_matches_brief(brief, vendor):
        return JsonResponse({"error": "Brief does not belong to this link"}, status=409)

    recipient_email = _normalize_contact_email(body.get("email") or "")
    if not recipient_email:
        return JsonResponse({"error": "email is required"}, status=400)
    if not _is_valid_email(recipient_email):
        return JsonResponse({"error": "Enter a valid email address"}, status=400)

    if recipient_email != brief.contact_email:
        Brief.objects.filter(id=brief.id).update(contact_email=recipient_email)
        brief.contact_email = recipient_email

    language = brief_emails.resolve_email_language(
        brief, request.headers.get("Accept-Language", "")
    )
    return _dispatch_send(brief, vendor, recipient_email, language)


@csrf_exempt
@require_http_methods(["POST"])
@public_endpoint
@conditional_ratelimit(key=client_ip_ratelimit_key, rate="6/h", method="POST")
def public_brief_ai_drafts(request):
    """Create an empty anonymous draft brief."""
    token = secrets.token_urlsafe(48)
    brief = Brief.objects.create(client=None, anonymous_token=token)
    return JsonResponse(
        {"briefId": str(brief.id), "token": token},
        status=201,
    )


@csrf_exempt
@require_http_methods(["POST"])
@public_endpoint
@conditional_ratelimit(key=client_ip_ratelimit_key, rate="3/h", method="POST")
def public_brief_ai_start(request, brief_id):
    brief = _get_brief_for_token(brief_id, request)
    if not brief:
        return JsonResponse({"error": "Brief not found"}, status=404)

    if brief.message_count > 0:
        return JsonResponse({"error": "Brief already started"}, status=409)

    body, error = _parse_json_body(request)
    if error:
        return error

    message, error = _validate_message(body)
    if error or message is None:
        return error or JsonResponse({"error": "Message is required"}, status=400)

    document_language, error = _parse_document_language(body)
    if error:
        return error

    token = request.headers.get("X-Brief-Token", "")
    attachment_ids = _parse_attachment_ids(body)

    brief_id_str = str(brief.id)
    task_id = str(uuid.uuid4())
    with transaction.atomic():
        user_message = ChatMessage.objects.create(
            brief=brief,
            user=None,
            anonymous_token=token,
            role="user",
            content=message,
        )
        update_kwargs: dict[str, Any] = {
            "message_count": F("message_count") + 1,
            "pending_task_id": task_id,
        }
        if document_language:
            update_kwargs["document_language"] = document_language
        Brief.objects.filter(id=brief.id).update(**update_kwargs)

        if attachment_ids:
            BriefAttachment.objects.filter(
                id__in=attachment_ids,
                brief=brief,
                message__isnull=True,
            ).update(message=user_message)

    transaction.on_commit(
        lambda: generate_first_reply_task.apply_async(
            args=[brief_id_str],
            task_id=task_id,
            link_error=clear_brief_pending_task.si(brief_id_str),
        )
    )
    return JsonResponse(
        {"briefId": brief_id_str, "taskId": task_id},
        status=201,
    )


@csrf_exempt
@require_http_methods(["GET"])
@public_endpoint
@conditional_ratelimit(key=client_ip_ratelimit_key, rate="60/m", method="GET")
def public_brief_ai_status(request, brief_id):
    brief = _get_brief_for_token(brief_id, request)
    if not brief:
        return JsonResponse({"error": "Brief not found"}, status=404)

    brief.refresh_from_db()
    if brief.pending_task_error:
        # The Send chain failed: its link_error stamped pending_task_error and
        # cleared the pending marker. Report "failed" (never "done") and clear
        # the flag so a fresh Send can re-arm cleanly.
        Brief.objects.filter(id=brief.id).update(pending_task_error="")
        logger.error("Send chain failed: brief_id=%s", brief_id)
        return JsonResponse({"status": "failed", "error": "Send failed"})
    if brief.pending_task_id:
        result = AsyncResult(brief.pending_task_id)
        if result.failed():
            return JsonResponse({"status": "failed", "error": "Generation failed"})
        return JsonResponse({"status": "pending"})

    return JsonResponse(
        {
            "status": "done",
            "result": serialize_brief_v3_detail(brief, user=_get_request_user(request)),
        }
    )


@csrf_exempt
@require_http_methods(["POST"])
@public_endpoint
@conditional_ratelimit(key=client_ip_ratelimit_key, rate="5/m", method="POST")
def public_brief_ai_chat(request, brief_id):
    brief = _get_brief_for_token(brief_id, request)
    if not brief:
        return JsonResponse({"error": "Brief not found"}, status=404)

    if brief.message_count >= MESSAGE_LIMIT_ANON:
        return JsonResponse({"error": "Message limit reached"}, status=429)

    if brief.total_cost_usd >= MAX_BRIEF_COST_USD:
        return JsonResponse(
            {
                "error": "Brief AI cost limit reached",
                "code": "cost_limit_reached",
                "limitUsd": str(MAX_BRIEF_COST_USD),
            },
            status=429,
        )

    body, error = _parse_json_body(request)
    if error:
        return error

    message, error = _validate_message(body)
    if error or message is None:
        return error or JsonResponse({"error": "Message is required"}, status=400)

    token = request.headers.get("X-Brief-Token", "")
    attachment_ids = _parse_attachment_ids(body)
    document_html = _parse_document_html(body)

    with transaction.atomic():
        user_message = ChatMessage.objects.create(
            brief=brief,
            user=None,
            anonymous_token=token,
            role="user",
            content=message,
        )
        Brief.objects.filter(id=brief.id).update(message_count=F("message_count") + 1)
        if attachment_ids:
            BriefAttachment.objects.filter(
                id__in=attachment_ids,
                brief=brief,
                message__isnull=True,
            ).update(message=user_message)

    brief.refresh_from_db()
    assistant_message, result, error_response = _process_chat(
        brief, user_message, message, current_document_html=document_html
    )
    if error_response or assistant_message is None or result is None:
        return error_response or JsonResponse(
            {"error": "Brief chat failed"}, status=500
        )

    brief.refresh_from_db()
    return JsonResponse(_build_chat_response(brief, assistant_message, result))


@csrf_exempt
@require_http_methods(["POST"])
@public_endpoint
@conditional_ratelimit(key=client_ip_ratelimit_key, rate="10/m", method="POST")
def public_brief_ai_attachments(request, brief_id):
    brief = _get_brief_for_token(brief_id, request)
    if not brief:
        return JsonResponse({"error": "Brief not found"}, status=404)

    existing = brief.attachments.count()
    uploaded, error = _validate_attachment_file(
        request, MAX_ATTACHMENTS_PER_BRIEF_ANON, existing
    )
    if error or uploaded is None:
        return error or JsonResponse({"error": "file is required"}, status=400)

    attachment = BriefAttachment.objects.create(
        brief=brief,
        file=uploaded,
        filename=(uploaded.name or "")[:255],
        mime_type=uploaded.content_type or "application/octet-stream",
        size_bytes=uploaded.size or 0,
    )
    return JsonResponse(serialize_brief_attachment(attachment), status=201)


@csrf_exempt
@require_http_methods(["DELETE"])
@public_endpoint
def public_brief_ai_attachment_delete(request, brief_id, attachment_id):
    brief = _get_brief_for_token(brief_id, request)
    if not brief:
        return JsonResponse({"error": "Brief not found"}, status=404)

    attachment = BriefAttachment.objects.filter(id=attachment_id, brief=brief).first()
    if not attachment:
        return JsonResponse({"error": "Attachment not found"}, status=404)

    if attachment.message_id is not None:
        return JsonResponse({"error": "Attachment already sent"}, status=409)

    try:
        attachment.file.delete(save=False)
    except Exception:
        logger.exception("Failed to delete attachment file %s", attachment.id)
    attachment.delete()
    return JsonResponse({"deleted": True})


@csrf_exempt
@require_http_methods(["POST"])
@public_endpoint
@conditional_ratelimit(key=client_ip_ratelimit_key, rate="10/m", method="POST")
def public_brief_ai_chat_transcribe(request, brief_id):
    brief = _get_brief_for_token(brief_id, request)
    if not brief:
        return JsonResponse({"error": "Brief not found"}, status=404)

    if brief.total_cost_usd >= MAX_BRIEF_COST_USD:
        return JsonResponse(
            {
                "error": "Brief AI cost limit reached",
                "code": "cost_limit_reached",
                "limitUsd": str(MAX_BRIEF_COST_USD),
            },
            status=429,
        )

    uploaded, declared_mime, error = _validate_audio_upload(request)
    if error or uploaded is None or declared_mime is None:
        return error or JsonResponse({"error": "audio is required"}, status=400)

    audio_bytes = uploaded.read()
    language = _resolve_transcribe_language(request, brief)
    duration_ms = _parse_client_duration_ms(request)
    dynamic_hints, static_hints = stt.build_phrase_hints(brief, language)

    try:
        result = stt.transcribe_audio(
            audio_bytes,
            declared_mime,
            language,
            dynamic_hints,
            static_hints,
        )
    except stt.TranscriptionError as ex:
        logger.warning("STT public failed: brief=%s code=%s", brief.id, ex.code)
        return _transcription_error_response(ex)

    logger.info(
        "STT public ok: brief=%s size=%s duration_ms=%s lang=%s mime=%s "
        "chars=%s hints=%s/%s",
        brief.id,
        len(audio_bytes),
        duration_ms,
        language,
        declared_mime,
        len(result["text"]),
        len(dynamic_hints),
        len(static_hints),
    )
    return JsonResponse(
        {
            "text": result["text"],
            "language": result["language"],
            "model": result["model"],
        },
    )


@csrf_exempt
@require_http_methods(["GET"])
@public_endpoint
@conditional_ratelimit(key=client_ip_ratelimit_key, rate="60/m", method="GET")
def public_brief_ai_detail(request, brief_id):
    brief = _get_brief_for_token(brief_id, request)
    if not brief:
        return JsonResponse({"error": "Brief not found"}, status=404)

    data = serialize_brief_v3(brief, user=_get_request_user(request))
    messages = brief.chat_messages.prefetch_related("feedbacks", "attachments").all()
    data["messages"] = [serialize_chat_message_v3(x) for x in messages]
    return JsonResponse(data)


@csrf_exempt
@require_http_methods(["POST"])
@require_groups("CLIENT")
@conditional_ratelimit(key="user", rate="20/h", method="POST")
def client_brief_ai_send(request, brief_id):
    """Authenticated Send: attach the brief to the chosen vendor as a project.

    No email is asked of the client (they are already in the cabinet); the
    vendor is resolved from the ``slug`` in the body. The same idempotent chain
    promotes/creates the project and notifies the vendor.
    """
    from aivus_backend.projects import brief_emails  # noqa: PLC0415

    brief = _get_brief_for_client(brief_id, request)
    if not brief:
        return JsonResponse({"error": "Brief not found"}, status=404)

    body, error = _parse_json_body(request)
    if error:
        return error

    slug = (body.get("slug") or "").strip()
    vendor = _resolve_vendor_by_slug(slug) if slug else None
    if not vendor:
        return JsonResponse(
            {"error": "This agency is no longer accepting briefs"}, status=404
        )

    language = brief_emails.resolve_email_language(
        brief, request.headers.get("Accept-Language", "")
    )
    return _dispatch_send(brief, vendor, "", language)


def _dispatch_finalize_if_ready(brief: Brief) -> bool:
    """Kick off finalize asynchronously when the ready brief has no documents.

    The branded anonymous flow shows and edits the document before Send, but the
    documents only exist after finalize. Running the LLM inside the request (and
    under a row lock) would block a worker and the brief row, so generation is
    dispatched to ``finalize_brief_task`` instead. Idempotency is guarded by
    ``pending_task_id``: an in-flight finalize is never dispatched twice. The
    front-end polls this endpoint until the documents appear.

    Returns True when generation is in progress (either freshly dispatched or
    already pending), False otherwise.
    """
    if brief.conversation_status != "ready_to_finalize":
        return False
    if brief.final_documents.exists():
        return False

    finalize_task_id = str(uuid.uuid4())
    dispatched = False
    with transaction.atomic():
        locked = Brief.objects.select_for_update().get(id=brief.id)
        if locked.conversation_status != "ready_to_finalize":
            return False
        if BriefFinalDocument.objects.filter(brief=locked).exists():
            return False
        if locked.pending_task_id:
            # A finalize is already running; the poller waits for it.
            return True
        Brief.objects.filter(id=locked.id).update(pending_task_id=finalize_task_id)
        dispatched = True

    if dispatched:
        brief_id_str = str(brief.id)
        transaction.on_commit(
            lambda: finalize_brief_task.apply_async(
                args=[brief_id_str],
                task_id=finalize_task_id,
                link_error=clear_brief_pending_task.si(brief_id_str),
            )
        )
    return True


@csrf_exempt
@require_http_methods(["GET"])
@public_endpoint
@conditional_ratelimit(key=client_ip_ratelimit_key, rate="60/m", method="GET")
def public_brief_ai_final_documents(request, brief_id):
    """Token-scoped read of the anonymous brief's final documents (S2-7).

    When the brief is ready but has no documents yet, finalize is dispatched
    asynchronously and the response carries ``generating: true`` with an empty
    document list; the front-end polls until the documents appear. No LLM call
    runs inside the request.
    """
    brief = _get_brief_for_token(brief_id, request)
    if not brief:
        return JsonResponse({"error": "Brief not found"}, status=404)

    generating = _dispatch_finalize_if_ready(brief)

    # Never expose the vendor outreach email to the anonymous client (PRD §5):
    # the brief is shown on the vendor's branded page and vendor_email holds the
    # vendor's outreach strategy and contacts.
    documents = list(
        brief.final_documents.filter(kind__in=ANON_VISIBLE_DOCUMENT_KINDS).order_by(
            "kind"
        )
    )
    return JsonResponse(
        {
            "briefId": str(brief.id),
            "conversationStatus": brief.conversation_status,
            "documents": [serialize_brief_final_document(x) for x in documents],
            "generating": generating and not documents,
        }
    )


@csrf_exempt
@require_http_methods(["PATCH"])
@public_endpoint
@conditional_ratelimit(key=client_ip_ratelimit_key, rate="60/m", method="PATCH")
def public_brief_ai_final_document_update(request, brief_id, document_id):
    """Token-scoped edit of an anonymous brief's final document (S2-7).

    Mirrors the authenticated editor PATCH so the white-label anonymous client
    can review and tweak the document before Send.
    """
    brief = _get_brief_for_token(brief_id, request)
    if not brief:
        return JsonResponse({"error": "Brief not found"}, status=404)

    # Once the brief is sent the vendor reads this very document (no copy), so an
    # anonymous edit after Send would silently change what the vendor sees. Lock
    # editing as soon as any vendor project reaches RFP.
    if _brief_already_sent(brief):
        return JsonResponse(
            {"error": "Brief already sent and can no longer be edited"}, status=409
        )

    # vendor_email is vendor PII and invisible to the anonymous client (PRD §5):
    # scope the lookup to the client-facing kinds so an anonymous edit can neither
    # read nor mutate it. An out-of-scope kind looks like a missing document (404),
    # which also avoids confirming the vendor_email exists.
    document = (
        BriefFinalDocument.objects.filter(id=document_id, brief=brief)
        .filter(kind__in=ANON_VISIBLE_DOCUMENT_KINDS)
        .first()
    )
    if not document:
        return JsonResponse({"error": "Document not found"}, status=404)

    body, error = _parse_json_body(request)
    if error:
        return error

    html = body.get("html")
    if not isinstance(html, str):
        return JsonResponse({"error": "html is required"}, status=400)
    if len(html) > MAX_FINAL_DOCUMENT_HTML_LENGTH:
        return JsonResponse({"error": "Document too large"}, status=400)

    document.html = sanitize_html(html)

    plain_text = body.get("plainText")
    if isinstance(plain_text, str):
        document.plain_text = plain_text[:MAX_FINAL_DOCUMENT_HTML_LENGTH]

    document.save(update_fields=["html", "plain_text", "updated_at"])
    return JsonResponse(serialize_brief_final_document(document))


@csrf_exempt
@require_http_methods(["POST"])
@require_groups("CLIENT")
def client_brief_ai_claim(request, brief_id):
    token = request.headers.get("X-Brief-Token", "")
    if not token:
        return JsonResponse({"error": "X-Brief-Token is required"}, status=400)

    user = _get_request_user(request)
    if not user:
        return JsonResponse({"error": "Client profile not found"}, status=403)

    claimable = Brief.objects.filter(
        id=brief_id,
        anonymous_token=token,
        client__isnull=True,
        deleted_at__isnull=True,
    ).first()
    if not claimable:
        return JsonResponse({"error": "Brief not found or already claimed"}, status=404)

    contact_email = (claimable.contact_email or "").strip()
    if contact_email and contact_email.casefold() != (user.email or "").casefold():
        return JsonResponse({"error": "Brief belongs to a different email"}, status=403)

    client = _get_client_safe(request) or _ensure_client_for_claim(request)
    if not client:
        return JsonResponse({"error": "Client profile not found"}, status=403)

    now = timezone.now()
    should_finalize = False
    finalize_task_id = ""
    with transaction.atomic():
        rows = Brief.objects.filter(
            id=brief_id,
            anonymous_token=token,
            client__isnull=True,
            deleted_at__isnull=True,
        ).update(
            client=client,
            anonymous_token=None,
            claimed_at=now,
        )
        if rows == 0:
            return JsonResponse(
                {"error": "Brief not found or already claimed"}, status=404
            )
        ChatMessage.objects.filter(
            brief_id=brief_id,
            anonymous_token=token,
        ).update(anonymous_token="")

        brief = Brief.objects.filter(id=brief_id).first()
        if not brief:
            return JsonResponse({"error": "Brief not found"}, status=404)

        should_finalize = (
            brief.conversation_status == "ready_to_finalize"
            and brief.status != "COMPLETED"
            and not brief.final_documents.exists()
        )
        if should_finalize:
            finalize_task_id = str(uuid.uuid4())
            Brief.objects.filter(id=brief.id).update(pending_task_id=finalize_task_id)

    payload = serialize_brief_v3_detail(brief, user=_get_request_user(request))

    if should_finalize:
        brief_id_str = str(brief.id)
        transaction.on_commit(
            lambda: finalize_brief_task.apply_async(
                args=[brief_id_str],
                task_id=finalize_task_id,
                link_error=clear_brief_pending_task.si(brief_id_str),
            )
        )
        payload["finalizingTaskId"] = finalize_task_id
        payload["pendingTaskId"] = finalize_task_id

    return JsonResponse(payload)
