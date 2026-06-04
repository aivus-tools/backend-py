import logging
import time
from decimal import Decimal
from typing import Any

from celery import shared_task
from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import F

from aivus_backend.projects.ai_brief_v3 import feedback_question_for
from aivus_backend.projects.ai_brief_v3 import generate_brief_title
from aivus_backend.projects.ai_brief_v3 import generate_final_documents
from aivus_backend.projects.ai_brief_v3 import process_brief_turn
from aivus_backend.projects.api.serializers import serialize_brief_v3
from aivus_backend.projects.api.serializers import serialize_brief_v3_detail
from aivus_backend.projects.attachments import MAX_ATTACHMENT_SIZE_BYTES
from aivus_backend.projects.attachments import WIX_FILE_HOST_SUFFIXES
from aivus_backend.projects.attachments import download_remote_file
from aivus_backend.projects.models import Brief
from aivus_backend.projects.models import BriefAttachment
from aivus_backend.projects.models import ChatMessage
from aivus_backend.projects.models import LLMCallTrace

logger = logging.getLogger(__name__)


@shared_task
def clear_brief_pending_task(brief_id: str) -> None:
    Brief.objects.filter(id=brief_id).update(pending_task_id="")


def persist_message_traces(chat_message: ChatMessage, traces: list[dict]) -> None:
    if not traces:
        return
    rows = [
        LLMCallTrace(
            message=chat_message,
            purpose=str(entry.get("purpose", "")),
            model=str(entry.get("model", "")),
            request_messages=entry.get("request_messages") or [],
            request_params=entry.get("request_params") or {},
            response_raw=str(entry.get("response_raw", "")),
            input_tokens=int(entry.get("input_tokens", 0) or 0),
            output_tokens=int(entry.get("output_tokens", 0) or 0),
            cost_usd=Decimal(str(entry.get("cost_usd", 0) or 0)),
            latency_ms=int(entry.get("latency_ms", 0) or 0),
            sequence=index,
        )
        for index, entry in enumerate(traces)
    ]
    LLMCallTrace.objects.bulk_create(rows)


def persist_final_document_traces(document, traces: list[dict]) -> None:
    if not traces:
        return
    rows = [
        LLMCallTrace(
            final_document=document,
            purpose=str(entry.get("purpose", "")),
            model=str(entry.get("model", "")),
            request_messages=entry.get("request_messages") or [],
            request_params=entry.get("request_params") or {},
            response_raw=str(entry.get("response_raw", "")),
            input_tokens=int(entry.get("input_tokens", 0) or 0),
            output_tokens=int(entry.get("output_tokens", 0) or 0),
            cost_usd=Decimal(str(entry.get("cost_usd", 0) or 0)),
            latency_ms=int(entry.get("latency_ms", 0) or 0),
            sequence=index,
        )
        for index, entry in enumerate(traces)
    ]
    LLMCallTrace.objects.bulk_create(rows)


@shared_task(
    soft_time_limit=180,
    time_limit=240,
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=1,
)
def generate_first_reply_task(brief_id: str) -> dict:
    try:
        brief = Brief.objects.get(id=brief_id)
    except Brief.DoesNotExist:
        logger.warning("Brief not found for first reply: brief_id=%s", brief_id)
        return {"error": "Brief not found"}

    if brief.message_count > 1:
        logger.warning("Brief already has assistant reply, skipping: %s", brief_id)
        Brief.objects.filter(id=brief.id).update(pending_task_id="")
        return serialize_brief_v3(brief)

    first_user_message = (
        brief.chat_messages.filter(role="user").order_by("created_at").first()
    )
    if not first_user_message:
        logger.warning("No user message found for brief %s", brief_id)
        Brief.objects.filter(id=brief.id).update(pending_task_id="")
        return {"error": "No user message"}

    attachments = list(
        BriefAttachment.objects.filter(
            brief=brief, message=first_user_message
        ).order_by("created_at")
    )

    result = process_brief_turn(
        brief=brief,
        user_message=first_user_message.content,
        attachments=attachments,
        history=[],
    )

    with transaction.atomic():
        if not brief.document_language and result.get("document_language"):
            Brief.objects.filter(id=brief.id).update(
                document_language=result["document_language"]
            )

        Brief.objects.filter(id=brief.id).update(
            conversation_status=result["conversation_status"],
            total_input_tokens=F("total_input_tokens") + result["input_tokens"],
            total_output_tokens=F("total_output_tokens") + result["output_tokens"],
            total_cost_usd=F("total_cost_usd") + Decimal(str(result["cost_usd"])),
            message_count=F("message_count") + 1,
            pending_task_id="",
        )

        chat_message = ChatMessage.objects.create(
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
        persist_message_traces(chat_message, result.get("traces", []))

    brief.refresh_from_db()
    return serialize_brief_v3(brief)


@shared_task(
    soft_time_limit=120,
    time_limit=180,
    max_retries=1,
)
def import_wix_attachments_task(brief_id: str, file_specs: list[dict]) -> dict:
    """Download files referenced by a Wix submission and attach them to the
    brief's first user message. Failures on individual files are logged and
    skipped so the follow-up first-reply task still runs."""
    try:
        brief = Brief.objects.get(id=brief_id)
    except Brief.DoesNotExist:
        logger.warning("Brief not found for Wix import: brief_id=%s", brief_id)
        return {"imported": 0}

    first_user_message = (
        brief.chat_messages.filter(role="user").order_by("created_at").first()
    )
    if not first_user_message:
        logger.warning("No user message for Wix import: brief_id=%s", brief_id)
        return {"imported": 0}

    extra_hosts = tuple(getattr(settings, "WIX_EXTRA_ALLOWED_HOSTS", []) or [])
    allowed_hosts = WIX_FILE_HOST_SUFFIXES + extra_hosts

    imported = 0
    for spec in file_specs or []:
        if not isinstance(spec, dict):
            continue
        url = spec.get("url") or ""
        filename = (spec.get("filename") or "attachment")[:255]
        downloaded = download_remote_file(
            url,
            allowed_host_suffixes=allowed_hosts,
            max_bytes=MAX_ATTACHMENT_SIZE_BYTES,
        )
        if downloaded is None:
            continue
        data, mime = downloaded
        BriefAttachment.objects.create(
            brief=brief,
            message=first_user_message,
            file=ContentFile(data, name=filename),
            filename=filename,
            mime_type=mime,
            size_bytes=len(data),
        )
        imported += 1

    return {"imported": imported}


@shared_task(
    soft_time_limit=180,
    time_limit=240,
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=1,
)
def finalize_brief_task(brief_id: str) -> dict:
    with transaction.atomic():
        try:
            brief = Brief.objects.select_for_update().get(id=brief_id)
        except Brief.DoesNotExist:
            logger.warning("Brief not found for finalization: brief_id=%s", brief_id)
            return {"error": "Brief not found"}

    started_at = time.monotonic()
    try:
        result = generate_final_documents(brief=brief)
    except Exception:
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        logger.exception(
            "Brief finalization failed: brief_id=%s elapsed_ms=%s",
            brief_id,
            elapsed_ms,
        )
        raise
    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    logger.info(
        "Brief finalization done: brief_id=%s elapsed_ms=%s "
        "input_tokens=%s output_tokens=%s cost_usd=%s",
        brief_id,
        elapsed_ms,
        result.get("input_tokens"),
        result.get("output_tokens"),
        result.get("cost_usd"),
    )

    # Auto-title is computed BEFORE the finalized state is exposed: clearing
    # pending_task_id is the signal the client uses to stop polling, so the title
    # must land in the same transaction. Otherwise the client renders the brief as
    # "Untitled" until a manual reload. Best-effort: a title failure must not block
    # finalization.
    new_title = ""
    if not brief.title:
        try:
            new_title = generate_brief_title(brief) or ""
        except Exception:
            logger.exception("auto-title failed brief_id=%s", brief.id)

    with transaction.atomic():
        update_kwargs: dict[str, Any] = {
            "status": "COMPLETED",
            "conversation_status": "finalized",
            "total_input_tokens": F("total_input_tokens") + result["input_tokens"],
            "total_output_tokens": F("total_output_tokens") + result["output_tokens"],
            "total_cost_usd": F("total_cost_usd") + Decimal(str(result["cost_usd"])),
            "pending_task_id": "",
        }
        if new_title and not brief.title:
            update_kwargs["title"] = new_title
        Brief.objects.filter(id=brief.id).update(**update_kwargs)
        for document in result["documents"]:
            persist_final_document_traces(document, result.get("traces", []))

    brief.refresh_from_db()

    # feedback-request chat message is best-effort — a failure here must not roll
    # back the finalize.
    try:
        already_asked = ChatMessage.objects.filter(
            brief=brief, kind=ChatMessage.KIND_FEEDBACK_REQUEST
        ).exists()
        if not already_asked:
            ChatMessage.objects.create(
                brief=brief,
                role="assistant",
                kind=ChatMessage.KIND_FEEDBACK_REQUEST,
                content=feedback_question_for(brief.document_language),
            )
    except Exception:
        logger.exception(
            "feedback_request message creation failed brief_id=%s", brief.id
        )

    return serialize_brief_v3_detail(brief)
