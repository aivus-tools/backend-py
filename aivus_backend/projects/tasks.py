import logging
from decimal import Decimal

from celery import shared_task
from django.db import transaction
from django.db.models import F

from aivus_backend.projects.ai_brief_v2 import finalize_brief
from aivus_backend.projects.ai_brief_v2 import process_brief_message
from aivus_backend.projects.api.serializers import serialize_brief_v2
from aivus_backend.projects.models import Brief
from aivus_backend.projects.models import ChatMessage

logger = logging.getLogger(__name__)


@shared_task(
    soft_time_limit=120,
    time_limit=180,
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=1,
)
def generate_brief_task(brief_id: str, user_message: str) -> dict:
    try:
        brief = Brief.objects.get(id=brief_id)
    except Brief.DoesNotExist:
        logger.warning("Brief not found for generation: brief_id=%s", brief_id)
        return {"error": "Brief not found"}

    if brief.conversation_phase != "initial":
        logger.warning("Brief already generated, skipping: brief_id=%s", brief_id)
        return serialize_brief_v2(brief)

    try:
        result = process_brief_message(
            user_message=user_message,
            brief_id=brief_id,
            document_sections=brief.document_sections,
            sections_status=brief.sections_status,
            archetypes=brief.archetypes,
            structured_data=brief.structured_data,
            conversation_phase="initial",
            questions_asked=[],
            history=[],
        )
    except Exception:
        logger.exception("Brief generation LLM call failed: brief_id=%s", brief_id)
        raise

    with transaction.atomic():
        Brief.objects.filter(id=brief_id).update(
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

    brief.refresh_from_db()
    return serialize_brief_v2(brief)


@shared_task(
    soft_time_limit=120,
    time_limit=180,
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

        if brief.status == "COMPLETED":
            logger.warning("Brief already finalized: brief_id=%s", brief_id)
            return serialize_brief_v2(brief)

        brief.status = "COMPLETED"
        brief.save(update_fields=["status", "updated_at"])

    try:
        result = finalize_brief(
            brief_id=brief_id,
            document_sections=brief.document_sections,
        )
    except Exception:
        logger.exception("Brief finalization LLM call failed: brief_id=%s", brief_id)
        Brief.objects.filter(id=brief_id).update(status="DRAFT")
        raise

    Brief.objects.filter(id=brief_id).update(
        structured_data=result["structured_data"],
        conversation_phase="complete",
        total_input_tokens=F("total_input_tokens") + result["input_tokens"],
        total_output_tokens=F("total_output_tokens") + result["output_tokens"],
        total_cost_usd=F("total_cost_usd") + Decimal(str(result["cost_usd"])),
    )

    brief.refresh_from_db()
    return serialize_brief_v2(brief)
