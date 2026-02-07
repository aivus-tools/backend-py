"""Serializers for projects API."""

from aivus_backend.projects.models import Brief
from aivus_backend.projects.models import ClientManager
from aivus_backend.projects.models import Offer
from aivus_backend.projects.models import Project
from aivus_backend.projects.models import ProjectCollaborator


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


def serialize_project(project: Project, include_relations: bool = True) -> dict:
    """Serialize Project model to dict."""
    result = {
        "id": str(project.id),
        "name": project.name,
        "vendorId": str(project.vendor_id),
        "briefId": str(project.brief_id) if project.brief_id else None,
        "teamId": str(project.team_id) if project.team_id else None,
        "status": project.status,
        # New fields
        "crmId": project.crm_id,
        "description": project.description,
        "clientId": str(project.client_id) if project.client_id else None,
        "clientName": project.client_name or (project.client.name if project.client else None),
        "irsEin": project.irs_ein,
        "brandName": project.brand_name,
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

