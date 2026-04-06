import json
import logging
import secrets
from decimal import Decimal

from celery.result import AsyncResult
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
from aivus_backend.projects.ai_brief_v2 import process_brief_message
from aivus_backend.projects.api.serializers import serialize_brief_feedback
from aivus_backend.projects.api.serializers import serialize_brief_share
from aivus_backend.projects.api.serializers import serialize_brief_share_public
from aivus_backend.projects.api.serializers import serialize_brief_v2
from aivus_backend.projects.api.serializers import serialize_brief_v2_detail
from aivus_backend.projects.api.serializers import serialize_chat_message_v2
from aivus_backend.projects.models import BRIEF_SECTION_KEYS
from aivus_backend.projects.models import Brief
from aivus_backend.projects.models import BriefFeedback
from aivus_backend.projects.models import BriefShare
from aivus_backend.projects.models import ChatMessage
from aivus_backend.projects.tasks import finalize_brief_task
from aivus_backend.projects.tasks import generate_brief_task
from aivus_backend.users.models import Client
from aivus_backend.users.models import User
from aivus_backend.users.models import UserSettings

logger = logging.getLogger(__name__)

MESSAGE_LIMIT_AUTH = 50
MESSAGE_LIMIT_ANON = 20
MAX_MESSAGE_LENGTH = 10000
MAX_SECTION_HTML_LENGTH = 50000
VALID_FEEDBACK_RATINGS = {"up", "down"}
MAX_FEEDBACK_COMMENT_LENGTH = 2000
BRIEF_SECTION_KEY_SET = set(BRIEF_SECTION_KEYS)
SUPPORTED_LANGUAGES = {"en", "ru", "es", "fr", "de", "it", "pt", "zh", "ja", "ko"}


def _get_brief_for_client(brief_id, request):
    client_id = request.user_data.get("client_id")
    if not client_id:
        return None
    return Brief.objects.filter(
        id=brief_id,
        client_id=client_id,
        deleted_at__isnull=True,
    ).first()


def _get_brief_for_token(brief_id, request):
    token = request.headers.get("X-Brief-Token", "")
    if not token:
        return None
    return Brief.objects.filter(
        id=brief_id,
        anonymous_token=token,
        deleted_at__isnull=True,
    ).first()


def _get_client_safe(request):
    client_id = request.user_data.get("client_id")
    if not client_id:
        return None
    return Client.objects.filter(id=client_id).first()


def _check_brief_mutable(brief):
    if brief.status == "COMPLETED":
        return JsonResponse({"error": "Brief is already finalized"}, status=409)
    return None


def _get_user_document_language(user_id) -> str:
    settings = UserSettings.objects.filter(user_id=user_id).first()
    if settings and settings.language:
        return settings.language
    return ""


def _parse_json_body(request):
    try:
        return json.loads(request.body), None
    except json.JSONDecodeError:
        return None, JsonResponse({"error": "Invalid JSON"}, status=400)


def _validate_message(body):
    message = body.get("message", "").strip()
    if not message:
        return None, JsonResponse({"error": "Message is required"}, status=400)
    if len(message) > MAX_MESSAGE_LENGTH:
        return None, JsonResponse({"error": "Message too long"}, status=400)
    return message, None


def _normalize_document_language(language: str) -> str:
    if not language:
        return ""
    lang = language.strip().lower()
    if lang in SUPPORTED_LANGUAGES:
        return lang
    return ""


@csrf_exempt
@require_http_methods(["POST"])
@require_groups("CLIENT")
@ratelimit(key="user", rate="20/m", method="POST")
def client_brief_ai_start(request):
    body, error = _parse_json_body(request)
    if error:
        return error

    message, error = _validate_message(body)
    if error:
        return error

    client = _get_client_safe(request)
    if not client:
        return JsonResponse({"error": "Client profile not found"}, status=403)

    user = User.objects.filter(id=request.user_data["id"]).first()
    if not user:
        return JsonResponse({"error": "User not found"}, status=401)

    brief = Brief.objects.create(client=client, status="DRAFT")

    ChatMessage.objects.create(
        brief=brief,
        user=user,
        role="user",
        content=message,
    )

    document_language = _get_user_document_language(user.id)
    task = generate_brief_task.delay(str(brief.id), message, document_language)

    return JsonResponse(
        {
            "briefId": str(brief.id),
            "taskId": task.id,
        },
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
                    "result": serialize_brief_v2(brief),
                }
            )
        logger.error(
            "Brief generation task failed: brief_id=%s error=%s",
            brief_id,
            result.result,
        )
        return JsonResponse(
            {
                "status": "failed",
                "error": "Brief generation failed. Please try again.",
            },
            status=500,
        )

    return JsonResponse({"status": "pending"})


@csrf_exempt
@require_http_methods(["POST"])
@require_groups("CLIENT")
@ratelimit(key="user", rate="20/m", method="POST")
def client_brief_ai_chat(request, brief_id):
    brief = _get_brief_for_client(brief_id, request)
    if not brief:
        return JsonResponse({"error": "Brief not found"}, status=404)

    if brief.message_count >= MESSAGE_LIMIT_AUTH:
        return JsonResponse({"error": "Message limit reached"}, status=429)

    body, error = _parse_json_body(request)
    if error:
        return error

    message, error = _validate_message(body)
    if error:
        return error

    user = User.objects.filter(id=request.user_data["id"]).first()
    if not user:
        return JsonResponse({"error": "User not found"}, status=401)

    ChatMessage.objects.create(
        brief=brief,
        user=user,
        role="user",
        content=message,
    )

    history = list(brief.chat_messages.values("role", "content").order_by("created_at"))
    document_language = _get_user_document_language(user.id)

    try:
        result = process_brief_message(
            user_message=message,
            brief_id=str(brief.id),
            document_sections=brief.document_sections,
            sections_status=brief.sections_status,
            archetypes=brief.archetypes,
            structured_data=brief.structured_data,
            conversation_phase=brief.conversation_phase,
            questions_asked=[],
            history=history,
            document_language=document_language,
        )
    except Exception:
        logger.exception("process_brief_message failed: brief_id=%s", brief_id)
        return JsonResponse({"error": "Brief generation failed"}, status=500)

    Brief.objects.filter(id=brief.id).update(
        document_sections=result["document_sections"],
        sections_status=result["sections_status"],
        archetypes=result["archetypes"],
        structured_data=result["structured_data"],
        conversation_phase=result["conversation_phase"],
        version=F("version") + 1,
        total_input_tokens=F("total_input_tokens") + result["input_tokens"],
        total_output_tokens=F("total_output_tokens") + result["output_tokens"],
        total_cost_usd=F("total_cost_usd") + Decimal(str(result["cost_usd"])),
        message_count=F("message_count") + 1,
    )
    brief.refresh_from_db()

    ChatMessage.objects.create(
        brief=brief,
        user=None,
        role="assistant",
        content=result["reply"],
        input_tokens=result["input_tokens"],
        output_tokens=result["output_tokens"],
        cost_usd=Decimal(str(result["cost_usd"])),
        model_used=result["model_used"],
        sections_changed=result["sections_changed"],
    )

    return JsonResponse(
        {
            "reply": result["reply"],
            "documentHtml": brief.render_document_html(),
            "sectionPatches": result["section_patches"],
            "sectionsChanged": result["sections_changed"],
            "sectionsStatus": result["sections_status"],
            "archetypes": result["archetypes"],
            "conversationPhase": result["conversation_phase"],
            "version": brief.version,
            "inputTokens": result["input_tokens"],
            "outputTokens": result["output_tokens"],
            "costUsd": str(Decimal(str(result["cost_usd"]))),
        }
    )


@csrf_exempt
@require_http_methods(["GET"])
@require_groups("CLIENT")
def client_brief_ai_detail(request, brief_id):
    brief = _get_brief_for_client(brief_id, request)
    if not brief:
        return JsonResponse({"error": "Brief not found"}, status=404)

    return JsonResponse(serialize_brief_v2_detail(brief))


@csrf_exempt
@require_http_methods(["PATCH"])
@require_groups("CLIENT")
def client_brief_ai_section(request, brief_id):
    brief = _get_brief_for_client(brief_id, request)
    if not brief:
        return JsonResponse({"error": "Brief not found"}, status=404)

    body, error = _parse_json_body(request)
    if error:
        return error

    section_key = body.get("sectionKey", "")
    html = body.get("html", "")
    expected_version = body.get("expectedVersion")

    if not section_key:
        return JsonResponse({"error": "sectionKey is required"}, status=400)

    if section_key not in BRIEF_SECTION_KEY_SET:
        return JsonResponse({"error": "Invalid section key"}, status=400)

    if len(html) > MAX_SECTION_HTML_LENGTH:
        return JsonResponse({"error": "Section HTML too long"}, status=400)

    sanitized = sanitize_html(html)

    with transaction.atomic():
        locked_brief = Brief.objects.select_for_update().get(
            id=brief_id, deleted_at__isnull=True
        )

        if expected_version is not None and expected_version != locked_brief.version:
            return JsonResponse(
                {
                    "error": "Version conflict",
                    "currentVersion": locked_brief.version,
                },
                status=409,
            )

        locked_brief.document_sections[section_key] = sanitized
        locked_brief.version += 1
        locked_brief.save(update_fields=["document_sections", "version", "updated_at"])

        return JsonResponse(
            {
                "version": locked_brief.version,
                "sectionKey": section_key,
            }
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
        return JsonResponse(
            {"error": "Invalid rating, must be 'up' or 'down'"}, status=400
        )

    comment = body.get("comment", "")
    if len(comment) > MAX_FEEDBACK_COMMENT_LENGTH:
        return JsonResponse({"error": "Comment too long"}, status=400)

    section_key = body.get("sectionKey", "")
    if section_key and section_key not in BRIEF_SECTION_KEY_SET:
        return JsonResponse({"error": "Invalid section key"}, status=400)

    message_id = body.get("messageId")
    chat_message = None
    if message_id:
        chat_message = ChatMessage.objects.filter(id=message_id, brief=brief).first()

    feedback = BriefFeedback.objects.create(
        brief=brief,
        message=chat_message,
        section_key=section_key,
        rating=rating,
        comment=comment,
        user=user,
    )

    return JsonResponse(serialize_brief_feedback(feedback), status=201)


@csrf_exempt
@require_http_methods(["POST"])
@require_groups("CLIENT")
@ratelimit(key="user", rate="5/m", method="POST")
def client_brief_ai_finalize(request, brief_id):
    brief = _get_brief_for_client(brief_id, request)
    if not brief:
        return JsonResponse({"error": "Brief not found"}, status=404)

    error = _check_brief_mutable(brief)
    if error:
        return error

    task = finalize_brief_task.delay(str(brief.id))
    return JsonResponse({"taskId": task.id})


@csrf_exempt
@require_http_methods(["POST"])
@public_endpoint
@ratelimit(key="ip", rate="3/h", method="POST")
def public_brief_ai_start(request):
    body, error = _parse_json_body(request)
    if error:
        return error

    message, error = _validate_message(body)
    if error:
        return error

    document_language = _normalize_document_language(body.get("documentLanguage", ""))

    token = secrets.token_urlsafe(48)
    brief = Brief.objects.create(
        client=None,
        status="DRAFT",
        anonymous_token=token,
    )

    ChatMessage.objects.create(
        brief=brief,
        user=None,
        anonymous_token=token,
        role="user",
        content=message,
    )

    task = generate_brief_task.delay(str(brief.id), message, document_language)

    return JsonResponse(
        {
            "briefId": str(brief.id),
            "token": token,
            "taskId": task.id,
        },
        status=201,
    )


@csrf_exempt
@require_http_methods(["GET"])
@public_endpoint
@ratelimit(key="ip", rate="60/m", method="GET")
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
                    "result": serialize_brief_v2(brief),
                }
            )
        return JsonResponse(
            {
                "status": "failed",
                "error": "Generation failed",
            },
            status=500,
        )

    return JsonResponse({"status": "pending"})


@csrf_exempt
@require_http_methods(["POST"])
@public_endpoint
@ratelimit(key="ip", rate="5/m", method="POST")
def public_brief_ai_chat(request, brief_id):
    brief = _get_brief_for_token(brief_id, request)
    if not brief:
        return JsonResponse({"error": "Brief not found"}, status=404)

    if brief.message_count >= MESSAGE_LIMIT_ANON:
        return JsonResponse({"error": "Message limit reached"}, status=429)

    body, error = _parse_json_body(request)
    if error:
        return error

    message, error = _validate_message(body)
    if error:
        return error

    document_language = _normalize_document_language(body.get("documentLanguage", ""))

    token = request.headers.get("X-Brief-Token", "")
    ChatMessage.objects.create(
        brief=brief,
        user=None,
        anonymous_token=token,
        role="user",
        content=message,
    )

    history = list(brief.chat_messages.values("role", "content").order_by("created_at"))

    try:
        result = process_brief_message(
            user_message=message,
            brief_id=str(brief.id),
            document_sections=brief.document_sections,
            sections_status=brief.sections_status,
            archetypes=brief.archetypes,
            structured_data=brief.structured_data,
            conversation_phase=brief.conversation_phase,
            questions_asked=[],
            history=history,
            document_language=document_language,
        )
    except Exception:
        logger.exception("process_brief_message failed: brief_id=%s", brief_id)
        return JsonResponse({"error": "Brief generation failed"}, status=500)

    Brief.objects.filter(id=brief.id).update(
        document_sections=result["document_sections"],
        sections_status=result["sections_status"],
        archetypes=result["archetypes"],
        structured_data=result["structured_data"],
        conversation_phase=result["conversation_phase"],
        version=F("version") + 1,
        total_input_tokens=F("total_input_tokens") + result["input_tokens"],
        total_output_tokens=F("total_output_tokens") + result["output_tokens"],
        total_cost_usd=F("total_cost_usd") + Decimal(str(result["cost_usd"])),
        message_count=F("message_count") + 1,
    )
    brief.refresh_from_db()

    ChatMessage.objects.create(
        brief=brief,
        user=None,
        anonymous_token=token,
        role="assistant",
        content=result["reply"],
        input_tokens=result["input_tokens"],
        output_tokens=result["output_tokens"],
        cost_usd=Decimal(str(result["cost_usd"])),
        model_used=result["model_used"],
        sections_changed=result["sections_changed"],
    )

    return JsonResponse(
        {
            "reply": result["reply"],
            "documentHtml": brief.render_document_html(),
            "sectionPatches": result["section_patches"],
            "sectionsChanged": result["sections_changed"],
            "sectionsStatus": result["sections_status"],
            "archetypes": result["archetypes"],
            "conversationPhase": result["conversation_phase"],
            "version": brief.version,
            "inputTokens": result["input_tokens"],
            "outputTokens": result["output_tokens"],
            "costUsd": str(Decimal(str(result["cost_usd"]))),
        }
    )


@csrf_exempt
@require_http_methods(["GET"])
@public_endpoint
@ratelimit(key="ip", rate="60/m", method="GET")
def public_brief_ai_detail(request, brief_id):
    brief = _get_brief_for_token(brief_id, request)
    if not brief:
        return JsonResponse({"error": "Brief not found"}, status=404)

    data = serialize_brief_v2(brief)
    messages = brief.chat_messages.prefetch_related("feedbacks").all()
    data["messages"] = [serialize_chat_message_v2(x) for x in messages]
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

    return JsonResponse(serialize_brief_v2(brief))


@csrf_exempt
@require_http_methods(["GET", "POST"])
@require_groups("CLIENT")
def client_brief_share_create(request, brief_id):
    brief = _get_brief_for_client(brief_id, request)
    if not brief:
        return JsonResponse({"error": "Brief not found"}, status=404)

    if brief.status != "COMPLETED":
        return JsonResponse({"error": "Brief must be finalized to share"}, status=400)

    if request.method == "GET":
        share = BriefShare.objects.filter(brief=brief).first()
        if not share:
            return JsonResponse({"error": "No share found"}, status=404)
        return JsonResponse(serialize_brief_share(share))

    user = User.objects.filter(id=request.user_data["id"]).first()
    share, _created = BriefShare.objects.get_or_create(
        brief=brief,
        defaults={"created_by": user},
    )
    return JsonResponse(serialize_brief_share(share), status=201 if _created else 200)


@csrf_exempt
@require_http_methods(["GET"])
@public_endpoint
def brief_share_get_public(request, token):
    share = (
        BriefShare.objects.select_related("brief")
        .filter(
            token=token,
            is_active=True,
        )
        .first()
    )
    if not share:
        return JsonResponse({"error": "Share not found or inactive"}, status=404)
    return JsonResponse(serialize_brief_share_public(share))


@csrf_exempt
@require_http_methods(["PATCH"])
@require_groups("CLIENT")
def brief_share_manage(request, token):
    client_id = request.user_data.get("client_id")
    share = (
        BriefShare.objects.select_related("brief")
        .filter(
            token=token,
            brief__client_id=client_id,
        )
        .first()
    )
    if not share:
        return JsonResponse({"error": "Share not found"}, status=404)

    body, error = _parse_json_body(request)
    if error:
        return error

    if "isActive" in body:
        share.is_active = bool(body["isActive"])
        share.save(update_fields=["is_active", "updated_at"])

    return JsonResponse(serialize_brief_share(share))


def _safe_filename(name: str) -> str:
    safe = "".join(c for c in name if c.isalnum() or c in " _-").strip()
    return safe[:80] or "Brief"


def _render_brief_pdf_response(brief):
    from urllib.parse import quote  # noqa: PLC0415

    from aivus_backend.projects.brief_pdf import render_brief_pdf  # noqa: PLC0415

    pdf_bytes = render_brief_pdf(brief)

    structured = brief.structured_data or {}
    name = _safe_filename(str(structured.get("projectName", "Brief")))
    filename = name + ".pdf"
    encoded = quote(filename)

    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = (
        f"attachment; filename=\"{filename}\"; filename*=UTF-8''{encoded}"
    )
    return response


@csrf_exempt
@require_http_methods(["GET"])
@require_groups("CLIENT")
def client_brief_ai_pdf(request, brief_id):
    brief = _get_brief_for_client(brief_id, request)
    if not brief:
        return JsonResponse({"error": "Brief not found"}, status=404)

    if brief.status != "COMPLETED":
        return JsonResponse({"error": "Brief must be finalized first"}, status=400)

    return _render_brief_pdf_response(brief)


@csrf_exempt
@require_http_methods(["GET"])
@public_endpoint
def brief_share_pdf(request, token):
    share = (
        BriefShare.objects.select_related("brief")
        .filter(token=token, is_active=True)
        .first()
    )
    if not share:
        return JsonResponse({"error": "Share not found or inactive"}, status=404)

    brief = share.brief
    if brief.status != "COMPLETED":
        return JsonResponse({"error": "Brief not available"}, status=400)

    return _render_brief_pdf_response(brief)
