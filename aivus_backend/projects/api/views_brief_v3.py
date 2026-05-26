"""REST views for AI brief v3."""

from __future__ import annotations

import json
import logging
import secrets
from decimal import Decimal
from typing import TYPE_CHECKING
from typing import Any

import magic
from celery.result import AsyncResult
from django.conf import settings
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
from aivus_backend.core.sanitize import sanitize_html
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
from aivus_backend.projects.models import Brief
from aivus_backend.projects.models import BriefAttachment
from aivus_backend.projects.models import BriefFeedback
from aivus_backend.projects.models import BriefFinalDocument
from aivus_backend.projects.models import BriefShare
from aivus_backend.projects.models import ChatMessage
from aivus_backend.projects.tasks import finalize_brief_task
from aivus_backend.projects.tasks import generate_first_reply_task
from aivus_backend.projects.tasks import persist_message_traces
from aivus_backend.users.models import Client
from aivus_backend.users.models import User

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
MAX_ATTACHMENT_SIZE_BYTES = 10 * 1024 * 1024
ALLOWED_MIME_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "text/plain",
}
VALID_FEEDBACK_RATINGS = {"up", "down"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def conditional_ratelimit(**ratelimit_kwargs):
    def decorator(func):
        if getattr(settings, "RATELIMIT_ENABLE", True):
            return ratelimit(**ratelimit_kwargs)(func)
        return func

    return decorator


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

    turn_runner = (
        process_finalized_turn
        if brief.conversation_status == "finalized"
        else process_brief_turn
    )

    try:
        result = turn_runner(
            brief=brief,
            user_message=message_text,
            attachments=attachments,
            history=history,
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


def _sniff_mime(uploaded) -> str:
    """Detect MIME type from file contents via libmagic, not the client-declared
    Content-Type. Rewinds the file pointer so the caller can still read it."""
    try:
        sample = uploaded.read(4096)
    finally:
        try:
            uploaded.seek(0)
        except Exception:
            logger.exception("Cannot rewind uploaded file")
    try:
        return magic.from_buffer(sample, mime=True) or ""
    except Exception:
        logger.exception("magic.from_buffer failed")
        return ""


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

    detected_mime = _sniff_mime(uploaded).lower()
    if detected_mime and detected_mime not in ALLOWED_MIME_TYPES:
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

    detected_mime = _sniff_mime(uploaded).lower()
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

    with transaction.atomic():
        user_message = ChatMessage.objects.create(
            brief=brief,
            user=user,
            role="user",
            content=message,
        )
        update_kwargs: dict[str, Any] = {"message_count": F("message_count") + 1}
        if document_language:
            update_kwargs["document_language"] = document_language
        Brief.objects.filter(id=brief.id).update(**update_kwargs)

        if attachment_ids:
            BriefAttachment.objects.filter(
                id__in=attachment_ids,
                brief=brief,
                message__isnull=True,
            ).update(message=user_message)

    task = generate_first_reply_task.delay(str(brief.id))
    return JsonResponse(
        {"briefId": str(brief.id), "taskId": task.id},
        status=201,
    )


@csrf_exempt
@require_http_methods(["GET"])
@require_groups("CLIENT")
def client_brief_ai_status(request, brief_id):
    brief = _get_brief_for_client(brief_id, request)
    if not brief:
        return JsonResponse({"error": "Brief not found"}, status=404)

    task_id = request.GET.get("taskId", "")
    if not task_id:
        return JsonResponse({"error": "taskId is required"}, status=400)

    result = AsyncResult(task_id)
    if result.ready():
        if result.successful():
            brief.refresh_from_db()
            return JsonResponse(
                {
                    "status": "done",
                    "result": serialize_brief_v3_detail(
                        brief, user=_get_request_user(request)
                    ),
                }
            )
        logger.error(
            "Generation task failed: brief_id=%s error=%s",
            brief_id,
            result.result,
        )
        return JsonResponse(
            {"status": "failed", "error": "Brief generation failed."},
            status=500,
        )
    return JsonResponse({"status": "pending"})


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
        brief, user_message, message
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

    task = finalize_brief_task.delay(str(brief.id))
    return JsonResponse({"taskId": task.id})


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
@conditional_ratelimit(key="ip", rate="120/m", method="GET")
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
@conditional_ratelimit(key="ip", rate="60/m", method="GET")
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


@csrf_exempt
@require_http_methods(["POST"])
@public_endpoint
@conditional_ratelimit(key="ip", rate="6/h", method="POST")
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
@conditional_ratelimit(key="ip", rate="3/h", method="POST")
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

    with transaction.atomic():
        user_message = ChatMessage.objects.create(
            brief=brief,
            user=None,
            anonymous_token=token,
            role="user",
            content=message,
        )
        update_kwargs: dict[str, Any] = {"message_count": F("message_count") + 1}
        if document_language:
            update_kwargs["document_language"] = document_language
        Brief.objects.filter(id=brief.id).update(**update_kwargs)

        if attachment_ids:
            BriefAttachment.objects.filter(
                id__in=attachment_ids,
                brief=brief,
                message__isnull=True,
            ).update(message=user_message)

    task = generate_first_reply_task.delay(str(brief.id))
    return JsonResponse(
        {"briefId": str(brief.id), "taskId": task.id},
        status=201,
    )


@csrf_exempt
@require_http_methods(["GET"])
@public_endpoint
@conditional_ratelimit(key="ip", rate="60/m", method="GET")
def public_brief_ai_status(request, brief_id):
    brief = _get_brief_for_token(brief_id, request)
    if not brief:
        return JsonResponse({"error": "Brief not found"}, status=404)

    task_id = request.GET.get("taskId", "")
    if not task_id:
        return JsonResponse({"error": "taskId is required"}, status=400)

    result = AsyncResult(task_id)
    if result.ready():
        if result.successful():
            brief.refresh_from_db()
            return JsonResponse(
                {
                    "status": "done",
                    "result": serialize_brief_v3_detail(
                        brief, user=_get_request_user(request)
                    ),
                }
            )
        return JsonResponse(
            {"status": "failed", "error": "Generation failed"}, status=500
        )
    return JsonResponse({"status": "pending"})


@csrf_exempt
@require_http_methods(["POST"])
@public_endpoint
@conditional_ratelimit(key="ip", rate="5/m", method="POST")
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
        brief, user_message, message
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
@conditional_ratelimit(key="ip", rate="10/m", method="POST")
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
@conditional_ratelimit(key="ip", rate="10/m", method="POST")
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
@conditional_ratelimit(key="ip", rate="60/m", method="GET")
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
def public_brief_ai_claim(request, brief_id):
    token = request.headers.get("X-Brief-Token", "")
    if not token:
        return JsonResponse({"error": "X-Brief-Token is required"}, status=400)

    client = _get_client_safe(request)
    if not client:
        return JsonResponse({"error": "Client profile not found"}, status=403)

    now = timezone.now()
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

    payload = serialize_brief_v3_detail(brief, user=_get_request_user(request))

    if (
        brief.conversation_status == "ready_to_finalize"
        and brief.status != "COMPLETED"
        and not brief.final_documents.exists()
    ):
        try:
            task = finalize_brief_task.delay(str(brief.id))
            payload["finalizingTaskId"] = task.id
        except Exception:
            logger.exception("Auto-finalize on claim failed brief_id=%s", brief.id)

    return JsonResponse(payload)
