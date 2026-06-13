"""Serializers for projects API."""

from aivus_backend.projects.models import Brief
from aivus_backend.projects.models import BriefAttachment
from aivus_backend.projects.models import BriefFeedback
from aivus_backend.projects.models import BriefFinalDocument
from aivus_backend.projects.models import BriefOffer
from aivus_backend.projects.models import BriefShare
from aivus_backend.projects.models import ChatMessage
from aivus_backend.projects.models import ClientManager
from aivus_backend.projects.models import Offer
from aivus_backend.projects.models import OfferDeliverable
from aivus_backend.projects.models import OfferScheduleEntry
from aivus_backend.projects.models import Project
from aivus_backend.projects.models import ProjectCollaborator
from aivus_backend.projects.models import RateCard
from aivus_backend.projects.models import RateCardItem
from aivus_backend.projects.models import Share
from aivus_backend.projects.models import Template
from aivus_backend.projects.services import reconstruct_details_from_entries


def serialize_brief(brief: Brief) -> dict:
    """Serialize Brief model to dict."""
    return {
        "id": str(brief.id),
        "uuid": str(brief.id),
        "status": brief.status,
        "details": brief.details,
        "structuredData": brief.structured_data,
        "clientId": str(brief.client_id) if brief.client_id else None,
        "createdAt": brief.created_at.isoformat() if brief.created_at else None,
        "updatedAt": brief.updated_at.isoformat() if brief.updated_at else None,
    }


def serialize_collaborator(collaborator: ProjectCollaborator) -> dict:
    """Serialize ProjectCollaborator model to dict."""
    return {
        "id": str(collaborator.id),
        "userId": str(collaborator.user_id) if collaborator.user_id else None,
        "name": collaborator.name,
        "email": collaborator.email,
        "role": collaborator.role,
    }


def serialize_client_manager(manager: ClientManager) -> dict:
    """Serialize ClientManager model to dict."""
    return {
        "id": str(manager.id),
        "name": manager.name,
        "position": manager.position,
    }


def serialize_project(project: Project, include_relations: bool = True) -> dict:  # noqa: FBT001, FBT002
    """Serialize Project model to dict."""
    brief = project.brief if project.brief_id else None
    result = {
        "id": str(project.id),
        "name": project.name,
        "vendorId": str(project.vendor_id),
        "briefId": str(project.brief_id) if project.brief_id else None,
        "briefConversationStatus": brief.conversation_status if brief else None,
        "hasContactEmail": bool(brief.contact_email) if brief else False,
        "teamId": str(project.team_id) if project.team_id else None,
        "status": project.status,
        # New fields
        "crmId": project.crm_id,
        "description": project.description,
        "clientId": str(project.client_id) if project.client_id else None,
        "clientName": project.client_name
        or (project.client.name if project.client else None),
        "irsEin": project.irs_ein,
        "brandName": project.brand_name,
        "agencyName": project.agency_name,
        "thumbnailUrl": project.thumbnail.url if project.thumbnail else None,
        "createdAt": project.created_at.isoformat() if project.created_at else None,
        "updatedAt": project.updated_at.isoformat() if project.updated_at else None,
    }

    if include_relations:
        result["collaborators"] = [
            serialize_collaborator(c) for c in project.collaborators.all()
        ]
        result["clientManagers"] = [
            serialize_client_manager(m) for m in project.client_managers.all()
        ]

    return result


def serialize_offer_deliverable(deliverable: OfferDeliverable) -> dict:
    return {
        "id": str(deliverable.id),
        "quantity": deliverable.quantity,
        "duration": deliverable.duration,
        "durationUnit": deliverable.duration_unit,
        "notes": deliverable.notes,
        "sortOrder": deliverable.sort_order,
    }


def serialize_offer_schedule_entry(entry: OfferScheduleEntry) -> dict:
    return {
        "id": str(entry.id),
        "phaseType": entry.phase_type,
        "days": entry.days,
        "hoursPerDay": entry.hours_per_day,
        "notes": entry.notes,
        "sortOrder": entry.sort_order,
    }


def _serialize_offer_meta_fields(offer: Offer) -> dict:
    return {
        "bidDate": offer.bid_date.isoformat() if offer.bid_date else None,
        "revision": offer.revision,
        "term": offer.term,
        "territory": offer.territory,
        "mediaPlacements": offer.media_placements,
        "coverPageNotes": offer.cover_page_notes,
        "assumptionsExclusions": offer.assumptions_exclusions,
        "fringesPercent": str(offer.fringes_percent),
        "handlingPercent": str(offer.handling_percent),
        "markupPercent": str(offer.markup_percent),
        "productionInsurancePercent": str(offer.production_insurance_percent),
        "productionFeePercent": str(offer.production_fee_percent),
        "postMarkupPercent": str(offer.post_markup_percent),
        "postInsurancePercent": str(offer.post_insurance_percent),
        "postTaxPercent": str(offer.post_tax_percent),
        "deliverables": [
            serialize_offer_deliverable(x)
            for x in offer.deliverables.filter(deleted_at__isnull=True)
        ],
        "scheduleEntries": [
            serialize_offer_schedule_entry(x)
            for x in offer.schedule_entries.filter(deleted_at__isnull=True)
        ],
    }


def serialize_offer(offer: Offer) -> dict:
    details = reconstruct_details_from_entries(offer)

    result = {
        "id": str(offer.id),
        "uuid": str(offer.id),
        "projectName": offer.project_name,
        "description": offer.description,
        "parentOfferId": str(offer.parent_offer_id) if offer.parent_offer_id else None,
        "projectId": str(offer.project_id) if offer.project_id else None,
        "status": offer.status,
        "cost": float(offer.cost) if offer.cost is not None else None,
        "profit": float(offer.profit) if offer.profit is not None else None,
        "details": details,
        "deadline": offer.deadline.isoformat() if offer.deadline else None,
        "source": offer.source,
        "isLocked": offer.is_locked,
        "createdAt": offer.created_at.isoformat() if offer.created_at else None,
        "updatedAt": offer.updated_at.isoformat() if offer.updated_at else None,
    }
    result.update(_serialize_offer_meta_fields(offer))
    return result


def serialize_offer_for_client(offer: Offer) -> dict:
    details = reconstruct_details_from_entries(offer)

    result = {
        "id": str(offer.id),
        "uuid": str(offer.id),
        "projectName": offer.project_name,
        "description": offer.description,
        "parentOfferId": str(offer.parent_offer_id) if offer.parent_offer_id else None,
        "projectId": str(offer.project_id) if offer.project_id else None,
        "status": offer.status,
        "details": details,
        "deadline": offer.deadline.isoformat() if offer.deadline else None,
        "source": offer.source,
        "isLocked": offer.is_locked,
        "createdAt": offer.created_at.isoformat() if offer.created_at else None,
        "updatedAt": offer.updated_at.isoformat() if offer.updated_at else None,
    }
    result.update(_serialize_offer_meta_fields(offer))
    return result


def serialize_share(share: Share) -> dict:
    """Serialize Share model to dict."""
    return {
        "id": str(share.id),
        "offerId": str(share.offer_id),
        "token": share.token,
        "isActive": share.is_active,
        "createdBy": str(share.created_by_id) if share.created_by_id else None,
        "createdAt": share.created_at.isoformat() if share.created_at else None,
        "updatedAt": share.updated_at.isoformat() if share.updated_at else None,
    }


def serialize_share_public(share: Share) -> dict:
    offer = share.offer
    details = reconstruct_details_from_entries(offer)

    offer_data = {
        "id": str(offer.id),
        "projectName": offer.project_name,
        "description": offer.description,
        "status": offer.status,
        "details": details,
        "projectId": str(offer.project_id) if offer.project_id else None,
        "deadline": offer.deadline.isoformat() if offer.deadline else None,
        "source": offer.source,
        "isLocked": offer.is_locked,
        "createdAt": offer.created_at.isoformat() if offer.created_at else None,
        "updatedAt": offer.updated_at.isoformat() if offer.updated_at else None,
    }
    offer_data.update(_serialize_offer_meta_fields(offer))

    result = {
        "id": str(share.id),
        "token": share.token,
        "isActive": share.is_active,
        "offer": offer_data,
        "vendor": None,
    }

    # Add vendor info if the offer has a project with a vendor
    if offer.project and offer.project.vendor:
        vendor = offer.project.vendor
        result["vendor"] = {
            "id": str(vendor.id),
            "name": vendor.name,
        }

    return result


def serialize_brief_offer(brief_offer: BriefOffer) -> dict:
    """Serialize BriefOffer model to dict."""
    return {
        "id": str(brief_offer.id),
        "briefId": str(brief_offer.brief_id),
        "offerId": str(brief_offer.offer_id),
        "linkedBy": str(brief_offer.linked_by_id) if brief_offer.linked_by_id else None,
        "createdAt": brief_offer.created_at.isoformat()
        if brief_offer.created_at
        else None,
    }


def serialize_template(template: Template) -> dict:
    """Serialize Template model to dict."""
    return {
        "id": str(template.id),
        "name": template.name,
        "vendorId": str(template.vendor_id),
        "sourceOfferId": str(template.source_offer_id)
        if template.source_offer_id
        else None,
        "details": template.details,
        "description": template.description,
        "metadata": template.metadata,
        "createdAt": template.created_at.isoformat() if template.created_at else None,
        "updatedAt": template.updated_at.isoformat() if template.updated_at else None,
    }


def serialize_rate_card_item(item: RateCardItem) -> dict:
    """Serialize RateCardItem model to dict."""
    return {
        "id": str(item.id),
        "rateCardId": str(item.rate_card_id),
        "entryId": str(item.entry_id) if item.entry_id else None,
        "itemName": item.item_name,
        "price": str(item.price),
        "unitId": str(item.unit_id) if item.unit_id else None,
        "unitLabel": item.unit_label,
        "createdAt": item.created_at.isoformat() if item.created_at else None,
        "updatedAt": item.updated_at.isoformat() if item.updated_at else None,
    }


def serialize_rate_card(rate_card: RateCard, include_items: bool = True) -> dict:  # noqa: FBT001, FBT002
    """Serialize RateCard model to dict."""
    result: dict = {
        "id": str(rate_card.id),
        "vendorId": str(rate_card.vendor_id),
        "name": rate_card.name,
        "createdAt": rate_card.created_at.isoformat() if rate_card.created_at else None,
        "updatedAt": rate_card.updated_at.isoformat() if rate_card.updated_at else None,
    }

    if include_items:
        result["items"] = [
            serialize_rate_card_item(item)
            for item in rate_card.items.filter(deleted_at__isnull=True)
        ]

    return result


def serialize_brief_with_offers(brief: Brief) -> dict:
    """Serialize Brief with linked offers count and status info for client dashboard."""
    brief_offers = brief.brief_offers.all()
    offers_count = brief_offers.count()

    return {
        "id": str(brief.id),
        "uuid": str(brief.id),
        "status": brief.status,
        "details": brief.details,
        "structuredData": brief.structured_data,
        "clientId": str(brief.client_id) if brief.client_id else None,
        "offersCount": offers_count,
        "createdAt": brief.created_at.isoformat() if brief.created_at else None,
        "updatedAt": brief.updated_at.isoformat() if brief.updated_at else None,
    }


def serialize_brief_detail(brief: Brief) -> dict:
    """Serialize Brief with full linked offers for detail view."""
    brief_offers = brief.brief_offers.select_related(
        "offer",
        "offer__project",
        "offer__project__vendor",
    ).all()

    linked_offers = []
    for bo in brief_offers:
        offer = bo.offer
        # QA4-023: Exclude cost/profit from client-facing brief detail
        offer_data: dict = {
            "id": str(offer.id),
            "projectName": offer.project_name,
            "description": offer.description,
            "status": offer.status,
            "deadline": offer.deadline.isoformat() if offer.deadline else None,
            "source": offer.source,
            "isLocked": offer.is_locked,
            "createdAt": offer.created_at.isoformat() if offer.created_at else None,
            "linkedAt": bo.created_at.isoformat() if bo.created_at else None,
        }
        if offer.project and offer.project.vendor:
            offer_data["vendor"] = {
                "id": str(offer.project.vendor.id),
                "name": offer.project.vendor.name,
            }
        else:
            offer_data["vendor"] = None
        linked_offers.append(offer_data)

    return {
        "id": str(brief.id),
        "uuid": str(brief.id),
        "status": brief.status,
        "details": brief.details,
        "clientId": str(brief.client_id) if brief.client_id else None,
        "offers": linked_offers,
        "offersCount": len(linked_offers),
        "createdAt": brief.created_at.isoformat() if brief.created_at else None,
        "updatedAt": brief.updated_at.isoformat() if brief.updated_at else None,
    }


def serialize_brief_attachment(attachment: BriefAttachment) -> dict:
    return {
        "id": str(attachment.id),
        "filename": attachment.filename,
        "mimeType": attachment.mime_type,
        "sizeBytes": attachment.size_bytes,
        "url": attachment.file.url if attachment.file else None,
        "createdAt": attachment.created_at.isoformat()
        if attachment.created_at
        else None,
    }


def serialize_chat_message_v3(message: ChatMessage) -> dict:
    feedback = message.feedbacks.first() if hasattr(message, "feedbacks") else None
    has_trace = (
        message.role == "assistant" and message.llm_traces.exists()
        if hasattr(message, "llm_traces")
        else False
    )
    attachments = (
        list(message.attachments.all()) if hasattr(message, "attachments") else []
    )
    return {
        "id": str(message.id),
        "role": message.role,
        "kind": getattr(message, "kind", "chat") or "chat",
        "content": message.content,
        "readyToFinalize": message.ready_to_finalize,
        "modelUsed": message.model_used,
        "inputTokens": message.input_tokens,
        "outputTokens": message.output_tokens,
        "costUsd": str(message.cost_usd),
        "hasTrace": has_trace,
        "attachments": [serialize_brief_attachment(x) for x in attachments],
        "feedback": serialize_brief_feedback(feedback) if feedback else None,
        "createdAt": message.created_at.isoformat() if message.created_at else None,
    }


def should_show_brief_cost(user) -> bool:
    from django.conf import settings as dj_settings  # noqa: PLC0415

    if getattr(dj_settings, "SHOW_BRIEF_COST_TO_ALL", False):
        return True
    return bool(user and getattr(user, "is_staff", False))


def serialize_brief_v3(brief: Brief, *, user=None) -> dict:
    return {
        "id": str(brief.id),
        "status": brief.status,
        "source": brief.source,
        "title": brief.title,
        "contactEmail": brief.contact_email,
        "contactName": brief.contact_name,
        "pendingTaskId": brief.pending_task_id or None,
        "documentLanguage": brief.document_language,
        "conversationStatus": brief.conversation_status,
        "totalInputTokens": brief.total_input_tokens,
        "totalOutputTokens": brief.total_output_tokens,
        "totalCostUsd": str(brief.total_cost_usd),
        "messageCount": brief.message_count,
        "showCost": should_show_brief_cost(user),
        "createdAt": brief.created_at.isoformat() if brief.created_at else None,
        "updatedAt": brief.updated_at.isoformat() if brief.updated_at else None,
        "claimedAt": brief.claimed_at.isoformat() if brief.claimed_at else None,
    }


def serialize_brief_v3_list_item(brief: Brief) -> dict:
    offers_count = brief.brief_offers.count()
    return {
        "id": str(brief.id),
        "status": brief.status,
        "title": brief.title,
        "conversationStatus": brief.conversation_status,
        "pendingTaskId": brief.pending_task_id or None,
        "messageCount": brief.message_count,
        "totalCostUsd": str(brief.total_cost_usd),
        "createdAt": brief.created_at.isoformat() if brief.created_at else None,
        "updatedAt": brief.updated_at.isoformat() if brief.updated_at else None,
        "claimedAt": brief.claimed_at.isoformat() if brief.claimed_at else None,
        "offersCount": offers_count,
    }


def serialize_brief_v3_detail(brief: Brief, *, user=None) -> dict:
    messages = brief.chat_messages.prefetch_related("feedbacks", "attachments").all()
    result = serialize_brief_v3(brief, user=user)
    result["messages"] = [serialize_chat_message_v3(x) for x in messages]
    return result


def serialize_brief_final_document(document: BriefFinalDocument) -> dict:
    return {
        "id": str(document.id),
        "kind": document.kind,
        "html": document.html,
        "plainText": document.plain_text,
        "createdAt": document.created_at.isoformat() if document.created_at else None,
        "updatedAt": document.updated_at.isoformat() if document.updated_at else None,
    }


def serialize_brief_feedback(feedback: BriefFeedback) -> dict:
    return {
        "id": str(feedback.id),
        "messageId": str(feedback.message_id) if feedback.message_id else None,
        "rating": feedback.rating,
        "comment": feedback.comment,
        "userId": str(feedback.user_id) if feedback.user_id else None,
        "createdAt": feedback.created_at.isoformat() if feedback.created_at else None,
    }


def serialize_brief_share(share: BriefShare) -> dict:
    return {
        "id": str(share.id),
        "briefId": str(share.brief_id),
        "token": share.token,
        "isActive": share.is_active,
        "createdAt": share.created_at.isoformat() if share.created_at else None,
        "updatedAt": share.updated_at.isoformat() if share.updated_at else None,
    }


def serialize_brief_share_public(share: BriefShare) -> dict:
    brief = share.brief
    documents = list(brief.final_documents.order_by("kind"))
    return {
        "token": share.token,
        "briefId": str(brief.id),
        "title": brief.title,
        "documentLanguage": brief.document_language,
        "conversationStatus": brief.conversation_status,
        "documents": [serialize_brief_final_document(x) for x in documents],
        "createdAt": share.created_at.isoformat() if share.created_at else None,
    }
