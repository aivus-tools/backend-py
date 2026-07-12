"""Tests for notification rules: mode, dedup, working hours, digest (S3-16)."""

from datetime import UTC
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from aivus_backend.email_agent import notifications
from aivus_backend.email_agent.events import NotificationEvent
from aivus_backend.email_agent.models import EmailAccount
from aivus_backend.email_agent.models import EmailAccountRole
from aivus_backend.email_agent.models import EmailThread
from aivus_backend.email_agent.models import NotificationLog
from aivus_backend.email_agent.models import OutboundDraft
from aivus_backend.email_agent.models import OutboundDraftKind
from aivus_backend.email_agent.models import VendorAgentProfile

pytestmark = pytest.mark.django_db

_NY = "America/New_York"
_WORKING_HOURS = {
    "timezone": _NY,
    "start": "09:00",
    "end": "18:00",
    "days": [1, 2, 3, 4, 5],
}


def _mailer():
    return patch.object(notifications, "send_to_recipient_email")


def test_dedup_suppresses_second_call_within_window(vendor):
    with _mailer() as mailer:
        first = notifications.notify(
            vendor, NotificationEvent.DRAFT_CREATED, {}, dedup_key="msg-1"
        )
        second = notifications.notify(
            vendor, NotificationEvent.DRAFT_CREATED, {}, dedup_key="msg-1"
        )

    assert first is not None
    assert second is None
    mailer.delay.assert_called_once()


def test_urgent_and_digest_mode_suppresses_info_events(vendor):
    VendorAgentProfile.objects.create(
        vendor=vendor,
        notification_rules={"mode": notifications.NOTIFICATION_MODE_URGENT_AND_DIGEST},
    )

    with _mailer() as mailer:
        log = notifications.notify(vendor, NotificationEvent.INBOUND_EMAIL, {})

    assert log is None
    mailer.delay.assert_not_called()


def test_urgent_and_digest_mode_still_delivers_draft_created(vendor):
    VendorAgentProfile.objects.create(
        vendor=vendor,
        notification_rules={"mode": notifications.NOTIFICATION_MODE_URGENT_AND_DIGEST},
    )

    with _mailer() as mailer:
        log = notifications.notify(vendor, NotificationEvent.DRAFT_CREATED, {})

    assert log is not None
    assert log.delivered is True
    mailer.delay.assert_called_once()


def test_is_within_working_hours_variants():
    tuesday_noon = datetime(2026, 7, 14, 16, 0, tzinfo=UTC)
    tuesday_night = datetime(2026, 7, 14, 3, 0, tzinfo=UTC)
    saturday_noon = datetime(2026, 7, 18, 16, 0, tzinfo=UTC)

    assert notifications.is_within_working_hours(_WORKING_HOURS, tuesday_noon) is True
    assert notifications.is_within_working_hours(_WORKING_HOURS, tuesday_night) is False
    assert notifications.is_within_working_hours(_WORKING_HOURS, saturday_noon) is False
    assert notifications.is_within_working_hours({}, tuesday_night) is True


def test_next_window_start_returns_next_morning():
    tuesday_night = datetime(2026, 7, 14, 3, 0, tzinfo=UTC)

    start = notifications.next_window_start(_WORKING_HOURS, tuesday_night)

    assert start is not None
    local = start.astimezone(ZoneInfo(_NY))
    assert local.hour == 9
    assert local.isoweekday() == 2
    assert notifications.next_window_start({}, tuesday_night) is None


def test_info_event_outside_hours_is_deferred(vendor):
    VendorAgentProfile.objects.create(vendor=vendor, working_hours=_WORKING_HOURS)
    night = datetime(2026, 7, 14, 3, 0, tzinfo=UTC)

    with (
        _mailer() as mailer,
        patch.object(notifications.timezone, "now", return_value=night),
    ):
        log = notifications.notify(vendor, NotificationEvent.INBOUND_EMAIL, {})

    assert log is not None
    assert log.delivered is False
    assert log.send_after is not None
    mailer.delay.assert_not_called()


def test_urgent_event_outside_hours_is_sent(vendor):
    VendorAgentProfile.objects.create(vendor=vendor, working_hours=_WORKING_HOURS)
    night = datetime(2026, 7, 14, 3, 0, tzinfo=UTC)

    with (
        _mailer() as mailer,
        patch.object(notifications.timezone, "now", return_value=night),
    ):
        log = notifications.notify(
            vendor, NotificationEvent.URGENT_LEAD, {}, urgent=True
        )

    assert log is not None
    assert log.delivered is True
    mailer.delay.assert_called_once()


def test_flush_due_notifications_sends_only_due_rows(vendor):
    now = datetime(2026, 7, 14, 13, 0, tzinfo=UTC)
    due = NotificationLog.objects.create(
        vendor=vendor,
        event=NotificationEvent.INBOUND_EMAIL,
        payload={"lines": ["x"]},
        delivered=False,
        send_after=now - notifications.timedelta(minutes=5),
    )
    not_due = NotificationLog.objects.create(
        vendor=vendor,
        event=NotificationEvent.INBOUND_EMAIL,
        payload={},
        delivered=False,
        send_after=now + notifications.timedelta(hours=2),
    )

    with _mailer() as mailer:
        sent = notifications.flush_due_notifications(now)

    assert sent == 1
    mailer.delay.assert_called_once()
    due.refresh_from_db()
    not_due.refresh_from_db()
    assert due.delivered is True
    assert due.send_after is None
    assert not_due.delivered is False


def test_build_daily_digest_collects_pending_drafts(vendor):
    thread = EmailThread.objects.create(
        vendor=vendor,
        provider_thread_id="t1",
        canonical_subject="Wedding film",
    )
    OutboundDraft.objects.create(
        thread=thread,
        kind=OutboundDraftKind.FIRST_REPLY,
        body="hi",
    )

    digest = notifications.build_daily_digest(vendor)

    assert digest is not None
    assert any("Wedding film" in line for line in digest["lines"])


def test_build_daily_digest_empty_returns_none(vendor):
    assert notifications.build_daily_digest(vendor) is None


def test_dispatch_due_digests_sends_at_local_digest_hour(vendor):
    EmailAccount.objects.create(
        vendor=vendor,
        role=EmailAccountRole.MONITOR,
        email="mon@x.io",
    )
    thread = EmailThread.objects.create(
        vendor=vendor, provider_thread_id="t1", canonical_subject="Lead"
    )
    OutboundDraft.objects.create(thread=thread, body="hi")
    nine_utc = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)

    with _mailer() as mailer:
        sent = notifications.dispatch_due_digests(nine_utc)

    assert sent == 1
    mailer.delay.assert_called_once()
    assert mailer.delay.call_args.kwargs["template"] == "emails/agent_digest_en.html"


def test_dispatch_due_digests_skips_off_hour(vendor):
    EmailAccount.objects.create(
        vendor=vendor, role=EmailAccountRole.MONITOR, email="mon@x.io"
    )
    noon_utc = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)

    with _mailer() as mailer:
        sent = notifications.dispatch_due_digests(noon_utc)

    assert sent == 0
    mailer.delay.assert_not_called()
