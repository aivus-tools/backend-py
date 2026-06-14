"""Custom handler and key helpers for django-ratelimit.

The handler turns ``Ratelimited`` into a JSON 429 instead of Django's default
403. The key helpers replace django-ratelimit's built-in ``key="ip"`` and
``key="user"``, both of which are broken behind our proxy chain (see
``resolve_client_ip`` and ``user_ratelimit_key``).
"""

from __future__ import annotations

import ipaddress

from django.conf import settings
from django.http import JsonResponse


def ratelimited_view(request, exception) -> JsonResponse:
    """RATELIMIT_VIEW target: render every throttle as JSON 429.

    django-ratelimit raises ``Ratelimited`` (a ``PermissionDenied`` subclass)
    when a limit is hit. Without a handler Django renders that as 403 Forbidden,
    which is indistinguishable from an authorization failure. The public funnel
    contract (PRD §8) expects 429 Too Many Requests so clients can back off
    correctly.
    """
    return JsonResponse({"error": "Too many requests"}, status=429)


def resolve_client_ip(request) -> str:
    """Single source of truth for the visitor's IP behind the reverse proxy.

    Real production chain: ``client -> Traefik -> Next.js (frontend container)
    -> Django``. Django is reachable *only* through the Next.js rewrite
    (``middleware.ts`` proxies ``/service/*`` onto ``API_URL``); there is no
    Traefik route straight to Django. The Next.js proxy is therefore the single,
    trusted entry point in front of Django.

    django-ratelimit's built-in ``key="ip"`` reads ``REMOTE_ADDR``, which on this
    path is the Next.js container's IP — every public visitor collapses into one
    bucket and a single abuser throttles everyone. The naive fix (left-most
    ``X-Forwarded-For``) is worse: that entry is fully client-supplied, so an
    attacker rotates a fake header and sidesteps every limit.

    The authoritative contract instead uses a dedicated header that only our
    trusted proxy sets:

    ``X-Aivus-Forwarded-Client`` (``HTTP_X_AIVUS_FORWARDED_CLIENT`` in Django's
    META). On every rewrite the Next.js proxy reads the right-most entry of the
    incoming ``X-Forwarded-For`` — the value Traefik appended, i.e. the real peer
    that connected to Traefik — and writes it into this header, overwriting any
    value the client tried to inject. Because Next.js is the only ingress to
    Django, a client cannot forge this header: anything it sends is overwritten
    before Django sees it. So when the header is present and parses as a valid IP
    we trust it outright, with no dependence on counting proxy hops.

    For direct or test calls that do not pass through Next.js the header is
    absent, and we fall back to the legacy ``RATELIMIT_TRUSTED_PROXY_COUNT``
    handling: trust exactly ``N`` hops, reading the client as the ``N``-th
    ``X-Forwarded-For`` entry from the right (``xff[-(N+1)]``); entries to the
    left of that are attacker-controlled and ignored. Defaults to ``0`` (trust no
    proxy) so a spoofed header falls back to the unforgeable ``REMOTE_ADDR``.

    If the deployment topology changes, update both this resolver and the
    front-end header handling together — they are two halves of one contract.
    """
    forwarded_client = request.META.get("HTTP_X_AIVUS_FORWARDED_CLIENT", "").strip()
    if forwarded_client and _is_valid_ip(forwarded_client):
        return forwarded_client

    remote_addr = request.META.get("REMOTE_ADDR", "") or "unknown"
    trusted = int(getattr(settings, "RATELIMIT_TRUSTED_PROXY_COUNT", 0) or 0)
    if trusted <= 0:
        return remote_addr

    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    parts = [p.strip() for p in forwarded.split(",") if p.strip()]
    # The header must carry at least one entry per trusted hop plus the client
    # itself; a shorter chain means the expected proxies did not all append, so we
    # refuse to read an attacker-shiftable position and fall back to REMOTE_ADDR.
    if len(parts) <= trusted:
        return remote_addr
    return parts[-(trusted + 1)]


def _is_valid_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def client_ip_ratelimit_key(group, request) -> str:
    """Rate-limit key keyed on the trusted client IP (see resolve_client_ip)."""
    return resolve_client_ip(request)


def user_ratelimit_key(group, request) -> str:
    """Rate-limit key keyed on the authenticated user.

    django-ratelimit's built-in ``key="user"`` reads ``request.user.pk``, but the
    HMAC middleware authenticates API requests by setting only ``request.user_data``
    and never ``request.user`` (it stays AnonymousUser with ``pk=None``). Built-in
    ``key="user"`` therefore collapses every authenticated caller into one shared
    bucket — a single user exhausts the limit for everyone, including endpoints
    that hit a paid LLM. This callable keys on the real user id from
    ``request.user_data`` instead, falling back to the trusted client IP when the
    request somehow lacks user context so the limit never silently disappears.
    """
    user_id = (getattr(request, "user_data", None) or {}).get("id")
    if user_id:
        return f"user:{user_id}"
    return f"ip:{resolve_client_ip(request)}"
