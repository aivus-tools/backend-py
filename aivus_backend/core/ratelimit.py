"""Custom handler for django-ratelimit so throttling returns 429, not 403.

django-ratelimit raises ``Ratelimited`` (a ``PermissionDenied`` subclass) when a
limit is hit. Without a handler Django renders that as 403 Forbidden, which is
indistinguishable from an authorization failure. The public funnel contract (PRD
§8) expects 429 Too Many Requests so clients can back off correctly. Wiring
``RATELIMIT_VIEW`` to this view plus ``RatelimitMiddleware`` turns every
``Ratelimited`` into a JSON 429.
"""

from __future__ import annotations

from django.http import JsonResponse


def ratelimited_view(request, exception) -> JsonResponse:
    return JsonResponse({"error": "Too many requests"}, status=429)
