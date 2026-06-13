"""Tests for the per-vendor webhook lead flow (Stage 2 S2-16)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from django.conf import settings as django_settings
from django.test import Client as DjangoTestClient
from django.urls import reverse

from aivus_backend.core.enums import BriefSource
from aivus_backend.core.enums import ProjectStatus
from aivus_backend.projects.models import Brief
from aivus_backend.projects.models import Project
from aivus_backend.users.models import User
from aivus_backend.users.models import Vendor
from aivus_backend.users.models import VendorWebhookKey


@pytest.fixture
def api_client() -> DjangoTestClient:
    return DjangoTestClient()


@pytest.fixture
def vendor_with_key(db):
    user = User.objects.create_user(
        email="webhook-vendor@example.com",
        password="p@ssw0rd",
        name="Webhook Vendor",
        group="VENDOR",
    )
    vendor = Vendor.objects.create(name="Hook Studio", owner=user)
    key_row = VendorWebhookKey.objects.create(vendor=vendor)
    return user, vendor, key_row


@pytest.fixture
def webhook_url() -> str:
    return reverse("projects_api:public_brief_ai_from_webhook")


def _auth(user) -> dict:
    return {
        "HTTP_X_API_KEY": django_settings.API_KEY,
        "HTTP_X_USER_ID": str(user.id),
        "HTTP_X_USER_GROUP": user.group,
    }


@pytest.mark.django_db
def test_webhook_valid_key_creates_brief_and_project(
    api_client, webhook_url, vendor_with_key
):
    _user, vendor, key_row = vendor_with_key
    with patch("aivus_backend.projects.api.views_brief_v3.transaction.on_commit"):
        response = api_client.post(
            webhook_url,
            data=json.dumps({"email": "lead@ext.com", "message": "Need a 30s spot"}),
            content_type="application/json",
            HTTP_X_AIVUS_WEBHOOK_KEY=key_row.key,
        )

    assert response.status_code == 201
    brief = Brief.objects.get(id=response.json()["briefId"])
    assert brief.source == BriefSource.WEBHOOK
    assert brief.contact_email == "lead@ext.com"

    project = Project.objects.get(brief=brief, vendor=vendor)
    assert project.status == ProjectStatus.RFP


@pytest.mark.django_db
def test_webhook_notifies_vendor(api_client, webhook_url, vendor_with_key):
    _user, vendor, key_row = vendor_with_key
    with (
        patch(
            "aivus_backend.projects.api.views_brief_v3.transaction.on_commit",
            side_effect=lambda func: func(),
        ),
        patch(
            "aivus_backend.projects.brief_emails.send_vendor_lead_email"
        ) as vendor_mock,
    ):
        response = api_client.post(
            webhook_url,
            data=json.dumps({"email": "lead@ext.com", "message": "Need a 30s spot"}),
            content_type="application/json",
            HTTP_X_AIVUS_WEBHOOK_KEY=key_row.key,
        )

    assert response.status_code == 201
    vendor_mock.assert_called_once()
    project = vendor_mock.call_args.args[0]
    assert project.vendor_id == vendor.id


@pytest.mark.django_db
def test_webhook_invalid_key_401(api_client, webhook_url, vendor_with_key):
    response = api_client.post(
        webhook_url,
        data=json.dumps({"message": "hi"}),
        content_type="application/json",
        HTTP_X_AIVUS_WEBHOOK_KEY="totally-wrong-key",
    )
    assert response.status_code == 401
    assert Brief.objects.count() == 0


@pytest.mark.django_db
def test_webhook_revoked_key_401(api_client, webhook_url, vendor_with_key):
    _user, _vendor, key_row = vendor_with_key
    VendorWebhookKey.objects.filter(id=key_row.id).update(is_active=False)
    response = api_client.post(
        webhook_url,
        data=json.dumps({"message": "hi"}),
        content_type="application/json",
        HTTP_X_AIVUS_WEBHOOK_KEY=key_row.key,
    )
    assert response.status_code == 401
    assert Brief.objects.count() == 0


@pytest.mark.django_db
def test_webhook_missing_message_still_creates_lead(
    api_client, webhook_url, vendor_with_key
):
    """SF-5: an external form may submit only contact details with no message. We
    must not lose the lead — the brief is created with a placeholder message and
    the project still lands at the vendor."""
    _user, vendor, key_row = vendor_with_key
    with patch("aivus_backend.projects.api.views_brief_v3.transaction.on_commit"):
        response = api_client.post(
            webhook_url,
            data=json.dumps({"email": "lead@ext.com"}),
            content_type="application/json",
            HTTP_X_AIVUS_WEBHOOK_KEY=key_row.key,
        )
    assert response.status_code == 201
    brief = Brief.objects.get(id=response.json()["briefId"])
    assert brief.contact_email == "lead@ext.com"
    first_message = brief.chat_messages.order_by("created_at").first()
    assert first_message is not None
    assert first_message.content.strip()
    assert Project.objects.filter(brief=brief, vendor=vendor).exists()


@pytest.mark.django_db
def test_webhook_blank_message_still_creates_lead(
    api_client, webhook_url, vendor_with_key
):
    """SF-5: a whitespace-only message is treated the same as a missing one."""
    _user, _vendor, key_row = vendor_with_key
    with patch("aivus_backend.projects.api.views_brief_v3.transaction.on_commit"):
        response = api_client.post(
            webhook_url,
            data=json.dumps({"email": "lead@ext.com", "message": "   "}),
            content_type="application/json",
            HTTP_X_AIVUS_WEBHOOK_KEY=key_row.key,
        )
    assert response.status_code == 201
    assert Brief.objects.count() == 1


@pytest.mark.django_db
def test_webhook_overlong_message_400(api_client, webhook_url, vendor_with_key):
    """An over-long message is still rejected even though empty ones are allowed."""
    from aivus_backend.projects.api.views_brief_v3 import MAX_MESSAGE_LENGTH

    _user, _vendor, key_row = vendor_with_key
    response = api_client.post(
        webhook_url,
        data=json.dumps(
            {"email": "lead@ext.com", "message": "x" * (MAX_MESSAGE_LENGTH + 1)}
        ),
        content_type="application/json",
        HTTP_X_AIVUS_WEBHOOK_KEY=key_row.key,
    )
    assert response.status_code == 400
    assert Brief.objects.count() == 0


@pytest.mark.django_db
def test_webhook_ip_rate_limit_fires_before_key_check(webhook_url):
    """An IP rate-limit must block before the webhook key is resolved so the
    endpoint cannot be used to brute-force keys.

    Rate limiting is disabled in the default test settings, so the production
    decorator is a no-op here. We re-create the same decorator stack with a 1/h
    IP rate to confirm the second request is rejected 429 before the key
    resolver is consulted. The cache is cleared so the limiter starts fresh.
    """
    from django.core.cache import cache
    from django.test import RequestFactory
    from django.test import override_settings
    from django_ratelimit.decorators import ratelimit
    from django_ratelimit.exceptions import Ratelimited

    from aivus_backend.projects.api import views_brief_v3

    cache.clear()
    factory = RequestFactory()

    with (
        override_settings(RATELIMIT_ENABLE=True),
        patch.object(
            views_brief_v3, "_verify_vendor_webhook_key", return_value=None
        ) as verify_mock,
    ):
        limited_view = ratelimit(key="ip", rate="1/h", method="POST", block=True)(
            views_brief_v3.public_brief_ai_from_webhook.__wrapped__
        )

        def _call():
            request = factory.post(
                webhook_url,
                data=json.dumps({"message": "hi"}),
                content_type="application/json",
            )
            return limited_view(request)

        first = _call()
        # The limiter raises Ratelimited before the key resolver is consulted on
        # the second request. RatelimitMiddleware.process_exception routes that
        # through RATELIMIT_VIEW into a 429 in real traffic; here we assert the
        # raise directly since the bare view is invoked without the middleware.
        with pytest.raises(Ratelimited):
            _call()

    assert first.status_code == 401
    assert verify_mock.call_count == 1


@pytest.mark.django_db
def test_webhook_vendor_rate_limit_trips_at_limit_and_is_per_vendor(vendor_with_key):
    """SF-4: the per-vendor 50/h limit caps a single vendor's webhook leads and is
    keyed by vendor_id, so one vendor hitting the cap does not throttle another.

    Rate limiting is disabled in the default test settings, so we enable it and
    drive _webhook_vendor_ratelimited directly. The cache is cleared so the limiter
    starts fresh. The first 50 calls for a vendor pass; the 51st trips. A second
    vendor with a separate id is unaffected.
    """
    from django.core.cache import cache
    from django.test import RequestFactory
    from django.test import override_settings

    from aivus_backend.projects.api.views_brief_v3 import _webhook_vendor_ratelimited

    _user, vendor, _key = vendor_with_key
    other_user = User.objects.create_user(
        email="other-webhook-vendor@example.com",
        password="p@ssw0rd",
        name="Other Vendor",
        group="VENDOR",
    )
    other_vendor = Vendor.objects.create(name="Other Studio", owner=other_user)

    cache.clear()
    factory = RequestFactory()

    def _request():
        return factory.post("/service/public/briefs/ai/from-webhook")

    with override_settings(RATELIMIT_ENABLE=True):
        # The 50/h allowance: the first 50 calls are allowed, the 51st trips.
        results = [_webhook_vendor_ratelimited(_request(), vendor) for _ in range(50)]
        assert not any(results)
        assert _webhook_vendor_ratelimited(_request(), vendor) is True
        # A different vendor is keyed separately and is not throttled.
        assert _webhook_vendor_ratelimited(_request(), other_vendor) is False


@pytest.mark.django_db
def test_webhook_returns_429_when_vendor_rate_limited(
    api_client, webhook_url, vendor_with_key
):
    """SF-4: when the per-vendor limit trips, the endpoint answers 429 after the
    key resolves and creates nothing."""
    _user, _vendor, key_row = vendor_with_key
    with patch(
        "aivus_backend.projects.api.views_brief_v3._webhook_vendor_ratelimited",
        return_value=True,
    ):
        response = api_client.post(
            webhook_url,
            data=json.dumps({"email": "lead@ext.com", "message": "hi"}),
            content_type="application/json",
            HTTP_X_AIVUS_WEBHOOK_KEY=key_row.key,
        )
    assert response.status_code == 429
    assert Brief.objects.count() == 0


@pytest.mark.django_db
def test_webhook_vendor_rate_limit_disabled_in_tests_by_default(vendor_with_key):
    """Without RATELIMIT_ENABLE the helper is a no-op so unrelated tests are not
    throttled by accumulated state."""
    from django.test import RequestFactory

    from aivus_backend.projects.api.views_brief_v3 import _webhook_vendor_ratelimited

    _user, vendor, _key = vendor_with_key
    request = RequestFactory().post("/service/public/briefs/ai/from-webhook")
    assert _webhook_vendor_ratelimited(request, vendor) is False


# --- key management endpoints ------------------------------------------------


@pytest.mark.django_db
def test_get_webhook_key_creates_lazily(api_client, vendor_with_key):
    user, vendor, _key = vendor_with_key
    VendorWebhookKey.objects.filter(vendor=vendor).delete()
    response = api_client.get(reverse("vendor-webhook-key"), **_auth(user))
    assert response.status_code == 200
    body = response.json()
    assert body["key"]
    assert body["isActive"] is True
    assert VendorWebhookKey.objects.filter(vendor=vendor).count() == 1


@pytest.mark.django_db
def test_rotate_webhook_key_changes_value(api_client, vendor_with_key):
    user, _vendor, key_row = vendor_with_key
    old_key = key_row.key
    response = api_client.post(reverse("vendor-webhook-key-rotate"), **_auth(user))
    assert response.status_code == 200
    new_key = response.json()["key"]
    assert new_key != old_key
    key_row.refresh_from_db()
    assert key_row.key == new_key
    assert key_row.rotated_at is not None


@pytest.mark.django_db
def test_rotated_old_key_stops_working(api_client, webhook_url, vendor_with_key):
    user, _vendor, key_row = vendor_with_key
    old_key = key_row.key
    api_client.post(reverse("vendor-webhook-key-rotate"), **_auth(user))

    response = api_client.post(
        webhook_url,
        data=json.dumps({"message": "hi"}),
        content_type="application/json",
        HTTP_X_AIVUS_WEBHOOK_KEY=old_key,
    )
    assert response.status_code == 401
