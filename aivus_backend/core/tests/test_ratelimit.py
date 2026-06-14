"""Tests that a hit rate limit returns 429, not the default 403 (PRD §8)."""

from __future__ import annotations

import json

import pytest
from django.core.cache import cache
from django.http import JsonResponse
from django.test import Client as DjangoTestClient
from django.test import override_settings
from django.urls import path
from django_ratelimit.decorators import ratelimit

from aivus_backend.core.decorators import public_endpoint
from aivus_backend.core.ratelimit import ratelimited_view


@public_endpoint
@ratelimit(key="ip", rate="1/m", method="GET", block=True)
def _limited_probe(request):
    return JsonResponse({"ok": True})


urlpatterns = [path("probe/", _limited_probe, name="probe")]


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
