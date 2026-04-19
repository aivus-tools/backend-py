import logging
from decimal import Decimal

from celery import shared_task
from django.db import transaction
from django.db.models import F

from aivus_backend.projects.ai_brief_v3 import feedback_question_for
from aivus_backend.projects.ai_brief_v3 import generate_brief_title
from aivus_backend.projects.ai_brief_v3 import generate_final_documents
from aivus_backend.projects.ai_brief_v3 import process_brief_turn
from aivus_backend.projects.api.serializers import serialize_brief_v3
from aivus_backend.projects.models import Brief
from aivus_backend.projects.models import BriefAttachment
from aivus_backend.projects.models import ChatMessage
from aivus_backend.projects.models import LLMCallTrace

logger = logging.getLogger(__name__)


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
        return serialize_brief_v3(brief)

    first_user_message = (
        brief.chat_messages.filter(role="user").order_by("created_at").first()
    )
    if not first_user_message:
        logger.warning("No user message found for brief %s", brief_id)
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

        if brief.conversation_status == "finalized":
            logger.warning("Brief already finalized: brief_id=%s", brief_id)
            return serialize_brief_v3(brief)

    try:
        result = generate_final_documents(brief=brief)
    except Exception:
        logger.exception("Brief finalization failed: brief_id=%s", brief_id)
        raise

    with transaction.atomic():
        Brief.objects.filter(id=brief.id).update(
            status="COMPLETED",
            conversation_status="finalized",
            total_input_tokens=F("total_input_tokens") + result["input_tokens"],
            total_output_tokens=F("total_output_tokens") + result["output_tokens"],
            total_cost_usd=F("total_cost_usd") + Decimal(str(result["cost_usd"])),
        )
        for document in result["documents"]:
            persist_final_document_traces(document, result.get("traces", []))

    brief.refresh_from_db()

    # Post-finalize side-effects: auto-title + feedback-request chat message.
    # Both are best-effort — a failure here must not roll back the finalize.
    try:
        title = generate_brief_title(brief)
        if title and not brief.title:
            brief.title = title
            brief.save(update_fields=["title", "updated_at"])
    except Exception:
        logger.exception("auto-title failed brief_id=%s", brief.id)

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

    return serialize_brief_v3(brief)
