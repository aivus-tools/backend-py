import logging
import time
from decimal import Decimal
from typing import Any

from celery import shared_task
from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import F

from aivus_backend.core.enums import ProjectStatus
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
from aivus_backend.projects.models import BriefFinalDocument
from aivus_backend.projects.models import BriefShare
from aivus_backend.projects.models import ChatMessage
from aivus_backend.projects.models import LLMCallTrace
from aivus_backend.projects.models import Project

logger = logging.getLogger(__name__)


@shared_task
def clear_brief_pending_task(brief_id: str) -> None:
    Brief.objects.filter(id=brief_id).update(pending_task_id="")


@shared_task
def set_brief_pending_task(brief_id: str, task_id: str) -> dict:
    """Re-assert the brief's pending marker mid-chain.

    Inside the Send chain the brief must stay "pending" until the project is
    promoted. The preceding finalize step now runs with keep_pending=True so the
    marker normally never drops, making this a no-op re-assert; it remains as a
    belt-and-braces guard for any path that clears the marker before promotion.
    A single atomic update sets the marker and clears any stale error so there is
    no clear-then-set window where the status endpoint could read it empty.
    """
    Brief.objects.filter(id=brief_id).update(
        pending_task_id=task_id, pending_task_error=""
    )
    return {"ok": True}


@shared_task
def mark_brief_send_failed_task(brief_id: str, task_id: str) -> None:
    """Record a failed Send chain so the status endpoint reports "failed".

    Used as the chain's ``link_error``. The Send chain is not dispatched with a
    single tracked id, so an ``AsyncResult`` on the pending marker can never see
    the failure. Persisting the failing chain id here is the source of truth:
    the status endpoint clears the pending marker and reports "failed" instead
    of silently flipping to "done". Only the chain that still owns the pending
    marker may stamp the error, so a stale retry cannot clobber a fresh Send.
    """
    Brief.objects.filter(id=brief_id, pending_task_id=task_id).update(
        pending_task_id="", pending_task_error=task_id
    )


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
def finalize_brief_task(brief_id: str, *, keep_pending: bool = False) -> dict:  # noqa: C901
    """Generate the brief's final documents and flip it to finalized.

    SF-1: when this task is the first step of a Send chain (``keep_pending=True``)
    it must not clear ``pending_task_id``. Clearing it would open a window between
    this task finishing and the next chain step re-arming the marker, during which
    the status endpoint would report ``done`` even though the project is still at
    DRAFT and no emails were sent — the client would redirect to success too early.
    Leaving the Send chain's marker in place keeps the brief ``pending`` until the
    tail clear step runs after the project is promoted to RFP. A standalone finalize
    (chat flow) keeps the default and clears the marker so polling can stop.
    """
    with transaction.atomic():
        try:
            brief = Brief.objects.select_for_update().get(id=brief_id)
        except Brief.DoesNotExist:
            logger.warning("Brief not found for finalization: brief_id=%s", brief_id)
            return {"error": "Brief not found"}

        # Idempotency guard: a brief that is already finalized and has documents
        # must never be re-finalized. generate_final_documents deletes and
        # recreates the documents, which would discard the existing copy and any
        # manual edits the client made before Send. This can happen when a Send is
        # pressed while a GET-triggered finalize is still in flight (MF-2 race) or
        # on a Celery retry. The select_for_update above serialises this check
        # against a concurrent finalize on the same row.
        if (
            brief.status == "COMPLETED"
            and brief.conversation_status == "finalized"
            and BriefFinalDocument.objects.filter(brief=brief).exists()
        ):
            logger.info(
                "Brief already finalized, skipping re-finalize: brief_id=%s", brief_id
            )
            if not keep_pending:
                Brief.objects.filter(id=brief.id).update(pending_task_id="")
            return serialize_brief_v3_detail(brief)

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
        }
        # SF-1: inside a Send chain (keep_pending) the marker must survive so the
        # status endpoint keeps reporting "pending" until the project is promoted.
        if not keep_pending:
            update_kwargs["pending_task_id"] = ""
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


@shared_task(
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def mark_project_sent_task(brief_id: str, vendor_id: str) -> dict:
    """Promote the vendor's lead project to RFP once the brief is finalized.

    Idempotent: a project already at RFP or beyond is left untouched so a
    double Send (double click, retry, browser back) never produces a second
    project. For the logged-in "pick an existing brief" flow no DRAFT project
    exists yet, so one is created directly at RFP.
    """
    promoted_statuses = {
        ProjectStatus.RFP,
        ProjectStatus.REVIEWING,
        ProjectStatus.ONGOING,
    }
    with transaction.atomic():
        brief = Brief.objects.filter(id=brief_id).first()
        if not brief:
            logger.warning("mark_project_sent: brief not found %s", brief_id)
            return {"ok": False}

        # Filter deleted_at to match the conditional unique constraint
        # (uniq_active_project_per_vendor_brief, deleted_at IS NULL). Without it
        # get_or_create would match a soft-deleted lead and resurrect it at RFP
        # while leaving deleted_at set, instead of creating a fresh active project.
        project, created = Project.objects.select_for_update().get_or_create(
            vendor_id=vendor_id,
            brief=brief,
            deleted_at__isnull=True,
            defaults={
                "name": (brief.title or "New brief lead")[:255],
                "status": ProjectStatus.RFP,
                "client_id": brief.client_id,
            },
        )
        if created:
            return {"ok": True, "projectId": str(project.id), "alreadySent": False}
        if project.status in promoted_statuses:
            return {"ok": True, "projectId": str(project.id), "alreadySent": True}

        project.status = ProjectStatus.RFP
        if not (project.name or "").strip() or project.name == "New brief lead":
            project.name = (brief.title or project.name or "New brief lead")[:255]
        if brief.client_id and not project.client_id:
            project.client_id = brief.client_id
        project.save(update_fields=["status", "name", "client", "updated_at"])

    return {"ok": True, "projectId": str(project.id), "alreadySent": False}


@shared_task(
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def send_emails_task(
    brief_id: str,
    vendor_id: str,
    recipient_email: str = "",
    language: str = "en",
) -> dict:
    """Create the public share and dispatch client + vendor lead emails.

    Runs after the project is at RFP. The client email is only sent when an
    email was provided (anonymous Send); the vendor email always fires. The
    BriefShare get_or_create reuses the same public share clients use today.
    """
    from django.utils import timezone  # noqa: PLC0415

    from aivus_backend.projects import brief_emails  # noqa: PLC0415

    brief = Brief.objects.filter(id=brief_id).first()
    if not brief:
        logger.warning("send_emails: brief not found %s", brief_id)
        return {"ok": False}

    # Idempotency guard against a duplicate Send chain slipping past the view-level
    # pending_task_id check (concurrency, Celery redelivery): stamp emails_sent_at
    # under a row lock and bail out if another run already did so. Without it the
    # client and vendor would each receive two emails.
    #
    # SF-9/SF-10: the BriefShare (whose token goes into the email) is created in
    # the SAME transaction that stamps emails_sent_at. Previously it was created
    # after the block, so a crash between committing the marker and creating the
    # share left the brief flagged "emails sent" with no share — and the retry,
    # seeing emails_sent_at set, bailed without ever creating the share or sending
    # the email. Binding the share to the marker keeps them consistent: either both
    # commit or neither does, and a retry that bails knows the share already exists.
    with transaction.atomic():
        project = (
            Project.objects.select_for_update()
            .filter(vendor_id=vendor_id, brief=brief, deleted_at__isnull=True)
            .select_related("vendor", "vendor__owner")
            .first()
        )
        if not project:
            logger.warning("send_emails: project not found brief=%s", brief_id)
            return {"ok": False}
        if project.emails_sent_at is not None:
            logger.info("send_emails: already sent brief=%s, skipping", brief_id)
            return {"ok": True, "alreadySent": True}
        share, _created = BriefShare.objects.get_or_create(brief=brief)
        project.emails_sent_at = timezone.now()
        project.save(update_fields=["emails_sent_at", "updated_at"])

    if recipient_email:
        try:
            brief_emails.send_client_lead_email(
                brief, recipient_email, share.token, language, project=project
            )
        except Exception:
            logger.exception("client lead email failed brief=%s", brief_id)

    try:
        brief_emails.send_vendor_lead_email(project, brief)
    except Exception:
        logger.exception("vendor lead email failed brief=%s", brief_id)

    return {"ok": True, "shareToken": share.token}
