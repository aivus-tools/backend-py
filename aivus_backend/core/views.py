"""Core views."""

from django.db.transaction import non_atomic_requests
from django.http import JsonResponse

from aivus_backend.core.decorators import public_endpoint


@public_endpoint
@non_atomic_requests
def healthz(request):
    """Liveness probe for Traefik and Docker healthchecks.

    @non_atomic_requests opts out of ATOMIC_REQUESTS so the probe never opens a
    DB transaction: it answers "is gunicorn serving HTTP", which is the signal
    the rolling deploy gates on. A database hiccup must not flap this probe and
    pull a serving container out of rotation.
    """
    return JsonResponse({"status": "ok"})
