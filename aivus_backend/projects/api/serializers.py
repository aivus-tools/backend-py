"""Serializers for projects API."""

from aivus_backend.projects.models import Brief
from aivus_backend.projects.models import Offer
from aivus_backend.projects.models import Project


def serialize_brief(brief: Brief) -> dict:
    """Serialize Brief model to dict."""
    return {
        "id": str(brief.id),
        "uuid": str(brief.id),
        "status": brief.status,
        "details": brief.details,
        "clientId": str(brief.client_id) if brief.client_id else None,
        "createdAt": brief.created_at.isoformat() if brief.created_at else None,
        "updatedAt": brief.updated_at.isoformat() if brief.updated_at else None,
    }


def serialize_project(project: Project) -> dict:
    """Serialize Project model to dict."""
    return {
        "id": str(project.id),
        "name": project.name,
        "vendorId": str(project.vendor_id),
        "briefId": str(project.brief_id) if project.brief_id else None,
        "teamId": str(project.team_id) if project.team_id else None,
        "status": project.status,
        "createdAt": project.created_at.isoformat() if project.created_at else None,
        "updatedAt": project.updated_at.isoformat() if project.updated_at else None,
    }


def serialize_offer(offer: Offer) -> dict:
    """Serialize Offer model to dict."""
    return {
        "id": str(offer.id),
        "uuid": str(offer.id),
        "projectName": offer.project_name,
        "parentOfferId": str(offer.parent_offer_id) if offer.parent_offer_id else None,
        "projectId": str(offer.project_id) if offer.project_id else None,
        "status": offer.status,
        "cost": offer.cost,
        "profit": offer.profit,
        "details": offer.details,
        "deadline": offer.deadline.isoformat() if offer.deadline else None,
        "source": offer.source,
        "isLocked": offer.is_locked,
        "createdAt": offer.created_at.isoformat() if offer.created_at else None,
        "updatedAt": offer.updated_at.isoformat() if offer.updated_at else None,
    }

