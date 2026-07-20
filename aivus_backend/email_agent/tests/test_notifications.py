"""Tests for the producer notification dispatcher (S3-15)."""

from unittest.mock import patch

import pytest

from aivus_backend.email_agent import notifications
from aivus_backend.email_agent.events import NotificationEvent
from aivus_backend.email_agent.models import NotificationChannel
from aivus_backend.email_agent.models import NotificationChannelType
from aivus_backend.email_agent.models import NotificationLog
from aivus_backend.email_agent.models import VendorAgentProfile
from aivus_backend.users.models import UserSettings
from aivus_backend.users.models import VendorSettings

pytestmark = pytest.mark.django_db


def _notify(vendor, event=NotificationEvent.DRAFT_CREATED, **kwargs):
    with patch.object(notifications, "send_to_recipient_email") as mailer:
        log = notifications.notify(vendor, event, {"lines": ["Subject: Hi"]}, **kwargs)
    return log, mailer


def test_email_channel_enqueues_platform_mailer_in_english(vendor):
    log, mailer = _notify(vendor)

    mailer.delay.assert_called_once()
    kwargs = mailer.delay.call_args.kwargs
    assert kwargs["recipient_email"] == vendor.owner.email
    assert kwargs["template"] == "emails/agent_notification_en.html"
    assert kwargs["subject"] == "Draft ready for review"
    assert kwargs["context"]["lines"] == ["Subject: Hi"]
    assert log is not None
    assert log.delivered is True
    assert log.error == ""


def test_notification_lines_are_stripped_of_planted_links(vendor):
    with patch.object(notifications, "send_to_recipient_email") as mailer:
        notifications.notify(
            vendor,
            NotificationEvent.URGENT_LEAD,
            {"lines": ["Budget: $50k confirm at http://evil.example/pay"]},
            urgent=True,
        )

    lines = mailer.delay.call_args.kwargs["context"]["lines"]
    assert not any("evil.example" in line for line in lines)
    assert any("[link removed]" in line for line in lines)


def test_deferred_notification_is_sanitized_on_flush(vendor):
    from django.utils import timezone

    VendorAgentProfile.objects.create(
        vendor=vendor,
        working_hours={"timezone": "UTC", "start": "09:00", "end": "18:00"},
    )
    NotificationLog.objects.create(
        vendor=vendor,
        event=NotificationEvent.INBOUND_EMAIL,
        payload={"lines": ["From www.evil.example/x"]},
        delivered=False,
        send_after=timezone.now(),
    )

    with patch.object(notifications, "send_to_recipient_email") as mailer:
        notifications.flush_due_notifications(timezone.now())

    lines = mailer.delay.call_args.kwargs["context"]["lines"]
    assert not any("evil.example" in line for line in lines)


def test_ru_vendor_gets_russian_template(vendor):
    UserSettings.objects.create(user=vendor.owner, language="ru")

    _log, mailer = _notify(vendor)

    kwargs = mailer.delay.call_args.kwargs
    assert kwargs["template"] == "emails/agent_notification_ru.html"
    assert kwargs["subject"] == "Черновик готов к проверке"


def test_recipient_precedence_producer_email_wins(vendor):
    VendorSettings.objects.create(vendor=vendor, lead_notification_email="lead@x.io")
    VendorAgentProfile.objects.create(vendor=vendor, producer_email="producer@x.io")

    _log, mailer = _notify(vendor)

    assert mailer.delay.call_args.kwargs["recipient_email"] == "producer@x.io"


def test_recipient_falls_back_to_lead_notification_email(vendor):
    VendorSettings.objects.create(vendor=vendor, lead_notification_email="lead@x.io")

    _log, mailer = _notify(vendor)

    assert mailer.delay.call_args.kwargs["recipient_email"] == "lead@x.io"


def test_unknown_channel_type_falls_back_to_email(vendor):
    NotificationChannel.objects.create(
        vendor=vendor,
        type=NotificationChannelType.TELEGRAM,
        config={"chat_id": "123"},
        enabled=True,
    )

    log, mailer = _notify(vendor)

    mailer.delay.assert_called_once()
    assert log is not None
    assert log.delivered is True
    assert "fallback to email" in log.error


def test_disabled_channel_is_skipped_for_implicit_email(vendor):
    NotificationChannel.objects.create(
        vendor=vendor,
        type=NotificationChannelType.TELEGRAM,
        enabled=False,
    )

    log, mailer = _notify(vendor)

    assert (
        mailer.delay.call_args.kwargs["template"] == "emails/agent_notification_en.html"
    )
    assert log is not None
    assert log.channel is None
    assert log.delivered is True


def test_email_delivery_failure_is_logged_not_raised(vendor):
    with patch.object(notifications, "send_to_recipient_email") as mailer:
        mailer.delay.side_effect = RuntimeError("broker down")
        log = notifications.notify(vendor, NotificationEvent.DRAFT_CREATED, {})

    assert log is not None
    assert log.delivered is False
    assert "broker down" in log.error


def test_explicit_channel_address_overrides_resolver(vendor):
    NotificationChannel.objects.create(
        vendor=vendor,
        type=NotificationChannelType.EMAIL,
        config={"address": "ops@x.io"},
        enabled=True,
    )

    _log, mailer = _notify(vendor)

    assert mailer.delay.call_args.kwargs["recipient_email"] == "ops@x.io"


def test_no_recipient_records_failure(vendor):
    vendor.owner.email = ""
    vendor.owner.save(update_fields=["email"])

    log, mailer = _notify(vendor)

    mailer.delay.assert_not_called()
    assert log is not None
    assert log.delivered is False
    assert "no notification recipient" in log.error


def test_dedup_key_is_persisted_on_the_log(vendor):
    log, _mailer = _notify(vendor, dedup_key="msg-1")

    assert log is not None
    assert log.dedup_key == "msg-1"
    assert NotificationLog.objects.filter(dedup_key="msg-1").count() == 1
