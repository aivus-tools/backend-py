"""API views for projects app."""

import json
import logging
from datetime import datetime
from datetime import timezone

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from aivus_backend.core.decorators import require_groups
from aivus_backend.projects.api.serializers import serialize_brief
from aivus_backend.projects.api.serializers import serialize_offer
from aivus_backend.projects.api.serializers import serialize_project
from aivus_backend.projects.models import Brief
from aivus_backend.projects.models import ClientManager
from aivus_backend.projects.models import Offer
from aivus_backend.projects.models import Project
from aivus_backend.projects.models import ProjectCollaborator
from aivus_backend.users.models import Client
from aivus_backend.users.models import Team
from aivus_backend.users.models import User
from aivus_backend.users.models import Vendor

logger = logging.getLogger(__name__)


# ==================== Projects API ====================


@csrf_exempt
@require_http_methods(["GET", "POST"])
@require_groups("VENDOR", "CLIENT", "SYSTEM")
def projects_list(request):
    """List all projects or create a new one."""
    if request.method == "GET":
        # Get vendor_id from headers
        vendor_id = request.META.get("HTTP_X_VENDOR_ID")
        if not vendor_id:
            return JsonResponse({"error": "Vendor ID required"}, status=400)

        projects = Project.objects.filter(vendor_id=vendor_id, deleted_at__isnull=True)
        return JsonResponse([serialize_project(p) for p in projects], safe=False)

    if request.method == "POST":
        try:
            data = json.loads(request.body)
            vendor_id = data.get("vendorId")
            brief_id = data.get("briefId")
            team_id = data.get("teamId")  # Optional
            name = data.get("name")
            status = data.get("status", "DRAFT")

            # New fields
            crm_id = data.get("crmId", "")
            description = data.get("description", "")
            client_id = data.get("clientId")
            client_name = data.get("clientName", "")
            irs_ein = data.get("irsEin", "")
            brand_name = data.get("brandName", "")
            collaborators = data.get("collaborators", [])
            client_managers = data.get("clientManagers", [])

            if not vendor_id or not name:
                return JsonResponse(
                    {"error": "vendorId and name are required"},
                    status=400,
                )

            # Verify vendor exists
            try:
                vendor = Vendor.objects.get(id=vendor_id)
            except Vendor.DoesNotExist:
                return JsonResponse({"error": "Vendor not found"}, status=404)

            # Verify team exists if provided
            team = None
            if team_id:
                try:
                    team = Team.objects.get(id=team_id)
                except Team.DoesNotExist:
                    return JsonResponse({"error": "Team not found"}, status=404)

            # Verify brief exists if provided
            brief = None
            if brief_id:
                try:
                    brief = Brief.objects.get(id=brief_id)
                except Brief.DoesNotExist:
                    return JsonResponse({"error": "Brief not found"}, status=404)

            # Verify client exists if provided
            client = None
            if client_id:
                try:
                    client = Client.objects.get(id=client_id)
                except Client.DoesNotExist:
                    return JsonResponse({"error": "Client not found"}, status=404)

            project = Project.objects.create(
                name=name,
                vendor=vendor,
                brief=brief,
                team=team,
                status=status,
                crm_id=crm_id,
                description=description,
                client=client,
                client_name=client_name,
                irs_ein=irs_ein,
                brand_name=brand_name,
            )

            # Create collaborators
            for collab in collaborators:
                user = None
                user_id = collab.get("userId")
                if user_id:
                    try:
                        user = User.objects.get(id=user_id)
                    except User.DoesNotExist:
                        pass  # Allow creating collaborator without user link

                ProjectCollaborator.objects.create(
                    project=project,
                    user=user,
                    name=collab.get("name", ""),
                    email=collab.get("email", ""),
                    role=collab.get("role", "internal_user"),
                )

            # Create client managers
            for manager in client_managers:
                ClientManager.objects.create(
                    project=project,
                    name=manager.get("name", ""),
                    position=manager.get("position", ""),
                )

            return JsonResponse(serialize_project(project), status=201)

        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        except Exception as e:
            logger.exception("Error creating project")
            return JsonResponse({"error": str(e)}, status=500)

    return JsonResponse({"error": "Method not allowed"}, status=405)


@csrf_exempt
@require_http_methods(["GET", "PUT", "PATCH", "DELETE"])
@require_groups("VENDOR", "CLIENT", "SYSTEM")
def project_detail(request, project_id):
    """Get, update, or delete a specific project."""
    try:
        project = Project.objects.get(id=project_id, deleted_at__isnull=True)
    except Project.DoesNotExist:
        return JsonResponse({"error": "Project not found"}, status=404)

    if request.method == "GET":
        return JsonResponse(serialize_project(project))

    if request.method in ["PUT", "PATCH"]:
        try:
            data = json.loads(request.body)

            if "name" in data:
                project.name = data["name"]
            if "status" in data:
                project.status = data["status"]
            if "briefId" in data:
                if data["briefId"]:
                    try:
                        brief = Brief.objects.get(id=data["briefId"])
                        project.brief = brief
                    except Brief.DoesNotExist:
                        return JsonResponse({"error": "Brief not found"}, status=404)
                else:
                    project.brief = None
            if "teamId" in data:
                if data["teamId"]:
                    try:
                        team = Team.objects.get(id=data["teamId"])
                        project.team = team
                    except Team.DoesNotExist:
                        return JsonResponse({"error": "Team not found"}, status=404)
                else:
                    project.team = None

            # New fields
            if "crmId" in data:
                project.crm_id = data["crmId"]
            if "description" in data:
                project.description = data["description"]
            if "clientId" in data:
                if data["clientId"]:
                    try:
                        client = Client.objects.get(id=data["clientId"])
                        project.client = client
                    except Client.DoesNotExist:
                        return JsonResponse({"error": "Client not found"}, status=404)
                else:
                    project.client = None
            if "clientName" in data:
                project.client_name = data["clientName"]
            if "irsEin" in data:
                project.irs_ein = data["irsEin"]
            if "brandName" in data:
                project.brand_name = data["brandName"]

            # Update collaborators if provided
            if "collaborators" in data:
                # Delete existing and recreate
                project.collaborators.all().delete()
                for collab in data["collaborators"]:
                    user = None
                    user_id = collab.get("userId")
                    if user_id:
                        try:
                            user = User.objects.get(id=user_id)
                        except User.DoesNotExist:
                            pass

                    ProjectCollaborator.objects.create(
                        project=project,
                        user=user,
                        name=collab.get("name", ""),
                        email=collab.get("email", ""),
                        role=collab.get("role", "internal_user"),
                    )

            # Update client managers if provided
            if "clientManagers" in data:
                # Delete existing and recreate
                project.client_managers.all().delete()
                for manager in data["clientManagers"]:
                    ClientManager.objects.create(
                        project=project,
                        name=manager.get("name", ""),
                        position=manager.get("position", ""),
                    )

            project.save()
            return JsonResponse(serialize_project(project))

        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        except Exception as e:
            logger.exception("Error updating project")
            return JsonResponse({"error": str(e)}, status=500)

    if request.method == "DELETE":
        project.deleted_at = datetime.now(timezone.utc)
        project.save()
        return JsonResponse({"message": "Project deleted"}, status=200)

    return JsonResponse({"error": "Method not allowed"}, status=405)


@csrf_exempt
@require_http_methods(["POST"])
@require_groups("VENDOR", "SYSTEM")
def project_thumbnail(request, project_id):
    """Upload project thumbnail image."""
    try:
        project = Project.objects.get(id=project_id, deleted_at__isnull=True)
    except Project.DoesNotExist:
        return JsonResponse({"error": "Project not found"}, status=404)

    if "thumbnail" not in request.FILES:
        return JsonResponse({"error": "No file provided"}, status=400)

    project.thumbnail = request.FILES["thumbnail"]
    project.save()

    return JsonResponse({
        "thumbnailUrl": project.thumbnail.url if project.thumbnail else None,
    })


# ==================== Briefs API ====================


@csrf_exempt
@require_http_methods(["GET", "POST"])
@require_groups("VENDOR", "CLIENT", "SYSTEM")
def briefs_list(request):
    """List all briefs or create a new one."""
    if request.method == "GET":
        briefs = Brief.objects.filter(deleted_at__isnull=True)
        return JsonResponse([serialize_brief(b) for b in briefs], safe=False)

    if request.method == "POST":
        try:
            data = json.loads(request.body)
            status = data.get("status", "DRAFT")
            details = data.get("details", {})
            client_id = data.get("clientId")

            brief = Brief.objects.create(
                status=status,
                details=details,
                client_id=client_id if client_id else None,
            )

            return JsonResponse(serialize_brief(brief), status=201)

        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        except Exception as e:
            logger.exception("Error creating brief")
            return JsonResponse({"error": str(e)}, status=500)

    return JsonResponse({"error": "Method not allowed"}, status=405)


@csrf_exempt
@require_http_methods(["GET", "PUT", "PATCH", "DELETE"])
@require_groups("VENDOR", "CLIENT", "SYSTEM")
def brief_detail(request, brief_id):
    """Get, update, or delete a specific brief."""
    try:
        brief = Brief.objects.get(id=brief_id, deleted_at__isnull=True)
    except Brief.DoesNotExist:
        return JsonResponse({"error": "Brief not found"}, status=404)

    if request.method == "GET":
        return JsonResponse(serialize_brief(brief))

    if request.method in ["PUT", "PATCH"]:
        try:
            data = json.loads(request.body)

            if "status" in data:
                brief.status = data["status"]
            if "details" in data:
                brief.details = data["details"]
            if "clientId" in data:
                brief.client_id = data["clientId"] if data["clientId"] else None

            brief.save()
            return JsonResponse(serialize_brief(brief))

        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        except Exception as e:
            logger.exception("Error updating brief")
            return JsonResponse({"error": str(e)}, status=500)

    if request.method == "DELETE":
        brief.deleted_at = datetime.now(timezone.utc)
        brief.save()
        return JsonResponse({"message": "Brief deleted"}, status=200)

    return JsonResponse({"error": "Method not allowed"}, status=405)


# ==================== Offers API ====================


@csrf_exempt
@require_http_methods(["GET", "POST"])
@require_groups("VENDOR", "CLIENT", "SYSTEM")
def offers_list(request):
    """List all offers or create a new one."""
    if request.method == "GET":
        # Get project_id from query params if provided
        project_id = request.GET.get("projectId")
        if project_id:
            offers = Offer.objects.filter(
                project_id=project_id,
                deleted_at__isnull=True,
            )
        else:
            offers = Offer.objects.filter(deleted_at__isnull=True)

        return JsonResponse([serialize_offer(o) for o in offers], safe=False)

    if request.method == "POST":
        try:
            data = json.loads(request.body)
            project_id = data.get("projectId")
            project_name = data.get("projectName")
            status = data.get("status", "DRAFT")
            details = data.get("details", {})
            deadline = data.get("deadline")
            source = data.get("source", "PLATFORM")
            is_locked = data.get("isLocked", False)
            cost = data.get("cost")
            profit = data.get("profit")

            if not project_id or not project_name or not deadline:
                return JsonResponse(
                    {"error": "projectId, projectName, and deadline are required"},
                    status=400,
                )

            # Verify project exists
            try:
                project = Project.objects.get(id=project_id)
            except Project.DoesNotExist:
                return JsonResponse({"error": "Project not found"}, status=404)

            # Parse deadline
            try:
                deadline_dt = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                return JsonResponse({"error": "Invalid deadline format"}, status=400)

            offer = Offer.objects.create(
                project=project,
                project_name=project_name,
                status=status,
                details=details,
                deadline=deadline_dt,
                source=source,
                is_locked=is_locked,
                cost=cost,
                profit=profit,
            )

            return JsonResponse(serialize_offer(offer), status=201)

        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        except Exception as e:
            logger.exception("Error creating offer")
            return JsonResponse({"error": str(e)}, status=500)

    return JsonResponse({"error": "Method not allowed"}, status=405)


@csrf_exempt
@require_http_methods(["GET", "PUT", "PATCH", "DELETE"])
@require_groups("VENDOR", "CLIENT", "SYSTEM")
def offer_detail(request, offer_id):
    """Get, update, or delete a specific offer."""
    try:
        offer = Offer.objects.get(id=offer_id, deleted_at__isnull=True)
    except Offer.DoesNotExist:
        return JsonResponse({"error": "Offer not found"}, status=404)

    if request.method == "GET":
        return JsonResponse(serialize_offer(offer))

    if request.method in ["PUT", "PATCH"]:
        try:
            data = json.loads(request.body)

            if "projectName" in data:
                offer.project_name = data["projectName"]
            if "status" in data:
                offer.status = data["status"]
            if "details" in data:
                offer.details = data["details"]
            if "deadline" in data:
                try:
                    deadline_dt = datetime.fromisoformat(
                        data["deadline"].replace("Z", "+00:00"),
                    )
                    offer.deadline = deadline_dt
                except (ValueError, AttributeError):
                    return JsonResponse(
                        {"error": "Invalid deadline format"},
                        status=400,
                    )
            if "source" in data:
                offer.source = data["source"]
            if "isLocked" in data:
                offer.is_locked = data["isLocked"]
            if "cost" in data:
                offer.cost = data["cost"]
            if "profit" in data:
                offer.profit = data["profit"]

            offer.save()
            return JsonResponse(serialize_offer(offer))

        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        except Exception as e:
            logger.exception("Error updating offer")
            return JsonResponse({"error": str(e)}, status=500)

    if request.method == "DELETE":
        offer.deleted_at = datetime.now(timezone.utc)
        offer.save()
        return JsonResponse({"message": "Offer deleted"}, status=200)

    return JsonResponse({"error": "Method not allowed"}, status=405)


@csrf_exempt
@require_http_methods(["GET"])
@require_groups("VENDOR", "CLIENT", "SYSTEM")
def offers_by_project(request, project_id):
    """Get all offers for a specific project."""
    try:
        project = Project.objects.get(id=project_id, deleted_at__isnull=True)
    except Project.DoesNotExist:
        return JsonResponse({"error": "Project not found"}, status=404)

    offers = Offer.objects.filter(project=project, deleted_at__isnull=True)
    return JsonResponse([serialize_offer(o) for o in offers], safe=False)

