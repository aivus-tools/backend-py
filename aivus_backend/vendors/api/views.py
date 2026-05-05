"""Vendors API views."""

import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from aivus_backend.core.decorators import require_groups
from aivus_backend.core.enums import Language
from aivus_backend.vendors.models import PreVendor

from .serializers import serialize_pre_vendor

logger = logging.getLogger(__name__)


@csrf_exempt
@require_http_methods(["GET"])
@require_groups("CLIENT", "VENDOR", "SYSTEM")
def list_pre_vendors(request):
    """
    List pre-vendors filtered by language.

    GET /api/v1/pre-vendors?language=ru
    """
    try:
        language = request.GET.get("language", Language.EN.value)
        if language not in {Language.EN.value, Language.RU.value}:
            return JsonResponse({"error": "Invalid language"}, status=400)

        queryset = PreVendor.objects.filter(language=language).order_by(
            "sort_order",
            "-created_at",
        )
        data = [serialize_pre_vendor(x, request) for x in queryset]
        return JsonResponse({"preVendors": data}, status=200)
    except Exception:
        logger.exception("Error listing pre-vendors")
        return JsonResponse({"error": "An internal error occurred"}, status=500)
