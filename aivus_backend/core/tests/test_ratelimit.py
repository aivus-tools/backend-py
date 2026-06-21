"""Tests that a hit rate limit returns 429, not the default 403 (PRD §8)."""

from __future__ import annotations

import json
from functools import wraps

import pytest
from django.core.cache import cache
from django.http import JsonResponse
from django.test import Client as DjangoTestClient
from django.test import override_settings
from django.urls import path
from django_ratelimit.decorators import ratelimit

from aivus_backend.core.decorators import public_endpoint
from aivus_backend.core.ratelimit import client_ip_ratelimit_key
from aivus_backend.core.ratelimit import ratelimited_view
from aivus_backend.core.ratelimit import user_ratelimit_key


@public_endpoint
@ratelimit(key="ip", rate="1/m", method="GET", block=True)
def _limited_probe(request):
    return JsonResponse({"ok": True})


@public_endpoint
@ratelimit(key=client_ip_ratelimit_key, rate="1/m", method="GET", block=True)
def _client_ip_probe(request):
    return JsonResponse({"ok": True})


def _attach_user_data(view_func):
    """Stand in for the HMAC middleware, which authenticates by attaching
    request.user_data (and never touching request.user) BEFORE the view — and so
    before the ratelimit key callable runs."""

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        user_id = request.headers.get("X-Probe-User-Id")
        if user_id:
            request.user_data = {"id": user_id}
        return view_func(request, *args, **kwargs)

    return wrapper


@public_endpoint
@_attach_user_data
@ratelimit(key=user_ratelimit_key, rate="1/m", method="GET", block=True)
def _user_probe(request):
    return JsonResponse({"ok": True})


urlpatterns = [
    path("probe/", _limited_probe, name="probe"),
    path("client-ip-probe/", _client_ip_probe, name="client-ip-probe"),
    path("user-probe/", _user_probe, name="user-probe"),
]


def test_ratelimited_view_returns_429():
    response = ratelimited_view(None, exception=None)
    assert response.status_code == 429
    assert json.loads(response.content)["error"] == "Too many requests"


@pytest.mark.django_db
@override_settings(
    ROOT_URLCONF="aivus_backend.core.tests.test_ratelimit",
    RATELIMIT_ENABLE=True,
)
def test_hit_rate_limit_returns_429_via_middleware():
    """The RatelimitMiddleware -> RATELIMIT_VIEW wiring must turn a hit limit into
    429. Without it django-ratelimit's Ratelimited (a PermissionDenied subclass)
    renders as a misleading 403."""
    cache.clear()
    client = DjangoTestClient()

    first = client.get("/probe/")
    assert first.status_code == 200

    second = client.get("/probe/")
    assert second.status_code == 429
    assert json.loads(second.content)["error"] == "Too many requests"


# A Redis URL that points at a closed port so any cache access fails fast.
_DEAD_REDIS = "redis://127.0.0.1:6399/15"


def _ratelimit_cache_settings(ignore_exceptions: bool) -> dict:
    return {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": _DEAD_REDIS,
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
                "IGNORE_EXCEPTIONS": ignore_exceptions,
            },
        },
    }


@pytest.mark.django_db
def test_ratelimit_fails_closed_with_dedicated_cache_alias():
    """SF-4: the dedicated rate-limit cache keeps IGNORE_EXCEPTIONS off, so a Redis
    outage raises instead of being silently swallowed. With RATELIMIT_FAIL_OPEN
    off that surfaces as an error rather than quietly disabling every limit — rate
    limiting fails CLOSED. The default cache (IGNORE_EXCEPTIONS=True) is the wrong
    place to count for exactly this reason, which is why production points
    RATELIMIT_USE_CACHE at this separate alias."""
    from django.test import RequestFactory
    from django.test import override_settings
    from django_ratelimit.core import is_ratelimited

    factory = RequestFactory()
    with (
        override_settings(
            RATELIMIT_ENABLE=True,
            RATELIMIT_USE_CACHE="default",
            RATELIMIT_FAIL_OPEN=False,
            CACHES=_ratelimit_cache_settings(ignore_exceptions=False),
        ),
        pytest.raises(Exception),
    ):
        is_ratelimited(
            request=factory.get("/"),
            group="sf4-closed",
            key="ip",
            rate="1/s",
            method="GET",
            increment=True,
        )


# ---------------------------------------------------------------------------
# RL-XFF-INVARIANT: X-Aivus-Forwarded-Client contract (resolve_client_ip)
#
# Real production chain: client -> Traefik -> Next.js -> Django. Django is
# reachable only via the Next.js rewrite, which stamps the authoritative
# X-Aivus-Forwarded-Client header with the real peer (right-most XFF entry that
# Traefik appended). resolve_client_ip must trust that header above everything
# else, independent of RATELIMIT_TRUSTED_PROXY_COUNT, and fall back to the legacy
# XFF/REMOTE_ADDR handling only when the header is absent or malformed.
# ---------------------------------------------------------------------------


def _request(remote_addr: str, *, forwarded_client=None, xff=None):
    from django.test import RequestFactory

    request = RequestFactory().get("/")
    request.META["REMOTE_ADDR"] = remote_addr
    request.META.pop("HTTP_X_FORWARDED_FOR", None)
    request.META.pop("HTTP_X_AIVUS_FORWARDED_CLIENT", None)
    if forwarded_client is not None:
        request.META["HTTP_X_AIVUS_FORWARDED_CLIENT"] = forwarded_client
    if xff is not None:
        request.META["HTTP_X_FORWARDED_FOR"] = xff
    return request


def test_forwarded_client_header_is_used_when_present():
    """The header our Next.js proxy sets is trusted outright, with no dependence
    on proxy-hop counting — this is the real production path."""
    from aivus_backend.core.ratelimit import resolve_client_ip

    request = _request("10.0.0.1", forwarded_client="198.51.100.9")
    with override_settings(RATELIMIT_TRUSTED_PROXY_COUNT=2):
        assert resolve_client_ip(request) == "198.51.100.9"


def test_forwarded_client_header_overrides_xff():
    """Even with an X-Forwarded-For present, the authoritative header wins. A
    client-supplied XFF cannot shift the resolved IP off our trusted value."""
    from aivus_backend.core.ratelimit import resolve_client_ip

    request = _request(
        "10.0.0.1",
        forwarded_client="198.51.100.9",
        xff="1.2.3.4, 5.6.7.8, 10.0.0.1",
    )
    with override_settings(RATELIMIT_TRUSTED_PROXY_COUNT=2):
        assert resolve_client_ip(request) == "198.51.100.9"


def test_forwarded_client_header_distinguishes_clients():
    """Two different real clients (different header values) resolve to different
    IPs, so the public funnel gets independent per-IP buckets (PRD §8)."""
    from aivus_backend.core.ratelimit import resolve_client_ip

    request_a = _request("10.0.0.1", forwarded_client="198.51.100.9")
    request_b = _request("10.0.0.1", forwarded_client="203.0.113.7")
    assert resolve_client_ip(request_a) != resolve_client_ip(request_b)


def test_forwarded_client_header_absent_falls_back_to_xff():
    """Direct/test calls that never pass through Next.js carry no header, so the
    legacy XFF hop-counting path still applies."""
    from aivus_backend.core.ratelimit import resolve_client_ip

    request = _request("10.0.0.1", xff="198.51.100.9, 172.16.0.5, 10.0.0.1")
    with override_settings(RATELIMIT_TRUSTED_PROXY_COUNT=2):
        assert resolve_client_ip(request) == "198.51.100.9"


def test_forwarded_client_header_absent_no_xff_falls_back_to_remote_addr():
    from aivus_backend.core.ratelimit import resolve_client_ip

    request = _request("198.51.100.4")
    with override_settings(RATELIMIT_TRUSTED_PROXY_COUNT=2):
        assert resolve_client_ip(request) == "198.51.100.4"


@pytest.mark.parametrize("bad", ["not-an-ip", "999.999.999.999", "", "   "])
def test_forwarded_client_header_malformed_falls_back(bad):
    """A malformed value (never produced by our proxy) is ignored and the legacy
    fallback kicks in, so a junk header cannot blank out the resolved IP."""
    from aivus_backend.core.ratelimit import resolve_client_ip

    request = _request("172.16.0.1", forwarded_client=bad, xff="9.9.9.9")
    with override_settings(RATELIMIT_TRUSTED_PROXY_COUNT=2):
        assert resolve_client_ip(request) == "172.16.0.1"


def test_forwarded_client_header_ignores_trusted_proxy_count_zero():
    """The header path is independent of RATELIMIT_TRUSTED_PROXY_COUNT, so even
    with the default 0 the production proxy's header is honoured."""
    from aivus_backend.core.ratelimit import resolve_client_ip

    request = _request("10.0.0.1", forwarded_client="198.51.100.9")
    with override_settings(RATELIMIT_TRUSTED_PROXY_COUNT=0):
        assert resolve_client_ip(request) == "198.51.100.9"


@pytest.mark.django_db
@override_settings(
    ROOT_URLCONF="aivus_backend.core.tests.test_ratelimit",
    RATELIMIT_ENABLE=True,
)
def test_distinct_forwarded_clients_get_independent_buckets():
    """End-to-end through the real limiter: two clients identified only by the
    X-Aivus-Forwarded-Client header that Next.js stamps get independent buckets,
    so one client cannot throttle another (cross-tenant DoS guard)."""
    cache.clear()
    client = DjangoTestClient()

    first = client.get(
        "/client-ip-probe/", HTTP_X_AIVUS_FORWARDED_CLIENT="198.51.100.9"
    )
    assert first.status_code == 200
    # Same client a second time hits its own 1/m limit.
    second = client.get(
        "/client-ip-probe/", HTTP_X_AIVUS_FORWARDED_CLIENT="198.51.100.9"
    )
    assert second.status_code == 429
    # A different client is in a separate bucket and is not throttled.
    other = client.get("/client-ip-probe/", HTTP_X_AIVUS_FORWARDED_CLIENT="203.0.113.7")
    assert other.status_code == 200


@pytest.mark.django_db
@override_settings(
    ROOT_URLCONF="aivus_backend.core.tests.test_ratelimit",
    RATELIMIT_ENABLE=True,
)
def test_same_forwarded_client_shares_one_bucket():
    """The same real client (same header) is throttled across requests even when
    the spoofable REMOTE_ADDR/XFF would suggest otherwise."""
    cache.clear()
    client = DjangoTestClient()

    first = client.get(
        "/client-ip-probe/", HTTP_X_AIVUS_FORWARDED_CLIENT="198.51.100.42"
    )
    assert first.status_code == 200
    second = client.get(
        "/client-ip-probe/", HTTP_X_AIVUS_FORWARDED_CLIENT="198.51.100.42"
    )
    assert second.status_code == 429


# ---------------------------------------------------------------------------
# RL-USER-KEY: per-user buckets under the HMAC middleware (user_ratelimit_key)
#
# The HMAC middleware sets only request.user_data, never request.user, so the
# built-in key="user" read AnonymousUser.pk=None and lumped every authenticated
# client into one shared bucket — a cross-tenant DoS on paid LLM/STT endpoints.
# user_ratelimit_key keys on the real user id instead.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(
    ROOT_URLCONF="aivus_backend.core.tests.test_ratelimit",
    RATELIMIT_ENABLE=True,
)
def test_distinct_users_get_independent_buckets():
    """End-to-end through the real limiter: two authenticated callers with
    different request.user_data ids get independent buckets, so one client cannot
    exhaust another's quota on a paid endpoint."""
    cache.clear()
    client = DjangoTestClient()

    first = client.get("/user-probe/", HTTP_X_PROBE_USER_ID="user-a")
    assert first.status_code == 200
    second = client.get("/user-probe/", HTTP_X_PROBE_USER_ID="user-a")
    assert second.status_code == 429
    # A different user is in a separate bucket and is not throttled.
    other = client.get("/user-probe/", HTTP_X_PROBE_USER_ID="user-b")
    assert other.status_code == 200


@pytest.mark.django_db
@override_settings(
    ROOT_URLCONF="aivus_backend.core.tests.test_ratelimit",
    RATELIMIT_ENABLE=True,
)
def test_same_user_shares_one_bucket():
    cache.clear()
    client = DjangoTestClient()

    first = client.get("/user-probe/", HTTP_X_PROBE_USER_ID="user-shared")
    assert first.status_code == 200
    second = client.get("/user-probe/", HTTP_X_PROBE_USER_ID="user-shared")
    assert second.status_code == 429
