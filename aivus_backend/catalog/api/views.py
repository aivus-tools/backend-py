"""Catalog API views."""

import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from aivus_backend.catalog.models import Category
from aivus_backend.catalog.models import Entry
from aivus_backend.catalog.models import Unit
from aivus_backend.core.decorators import require_groups

from .serializers import serialize_category
from .serializers import serialize_entry
from .serializers import serialize_unit

logger = logging.getLogger(__name__)


@csrf_exempt
@require_http_methods(["GET"])
@require_groups("VENDOR", "CLIENT", "SYSTEM")
def get_categories(request):
    """
    Get all categories.

    GET /api/v1/categories
    """
    try:
        # QA3-016: Filter out soft-deleted records
        categories = Category.objects.filter(deleted_at__isnull=True).order_by(
            "level", "name"
        )
        data = [serialize_category(cat) for cat in categories]
        return JsonResponse(data, safe=False, status=200)
    except Exception:
        logger.exception("Error getting categories")
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@csrf_exempt
@require_http_methods(["GET"])
@require_groups("VENDOR", "CLIENT", "SYSTEM")
def get_entries(request):
    """
    Get all entries.

    GET /api/v1/entries
    Query params:
      - full: if 'true', return full entry data with units
    """
    try:
        # QA3-016: Filter out soft-deleted records
        entries = (
            Entry.objects.select_related("category")
            .prefetch_related("entry_units__unit")
            .filter(is_approved=True, deleted_at__isnull=True)
            .order_by("name")
        )

        # Check if full data requested
        full = request.GET.get("full", "false").lower() == "true"

        data = [serialize_entry(entry, include_units=full) for entry in entries]
        return JsonResponse({"entries": data}, status=200)

    except Exception:
        logger.exception("Error getting entries")
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@csrf_exempt
@require_http_methods(["GET"])
@require_groups("VENDOR", "CLIENT", "SYSTEM")
def get_entry(request, entry_id):
    """
    Get entry by ID with full details including units.

    GET /api/v1/entries/:id
    """
    try:
        # QA3-016: Filter out soft-deleted records
        entry = (
            Entry.objects.select_related("category")
            .prefetch_related("entry_units__unit")
            .get(id=entry_id, deleted_at__isnull=True)
        )

        data = serialize_entry(entry, include_units=True)
        return JsonResponse(data, status=200)

    except Entry.DoesNotExist:
        return JsonResponse({"error": "Entry not found"}, status=404)
    except Exception:
        logger.exception("Error getting entry")
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@csrf_exempt
@require_http_methods(["GET"])
@require_groups("VENDOR", "CLIENT", "SYSTEM")
def get_units(request):
    """
    Get all units.

    GET /api/v1/units
    """
    try:
        # QA3-016: Filter out soft-deleted records
        units = Unit.objects.filter(deleted_at__isnull=True).order_by("name")
        data = [serialize_unit(unit) for unit in units]
        return JsonResponse({"units": data}, status=200)
    except Exception:
        logger.exception("Error getting units")
        return JsonResponse({"error": "An internal error occurred"}, status=500)
