"""Producer notification dispatcher for the email agent (Stage 3, S3-15).

A single ``notify`` entry point delivers producer notifications through a
polymorphic channel layer so Telegram/Discord can be added later without
touching call sites. The MVP ships the email channel, delivered through the
platform mailer (Resend/anymail via ``send_to_recipient_email``) — a different
transport from the client-facing replies, which go out over the vendor's own
SMTP. An unavailable or unimplemented channel degrades gracefully to email and
records the failure.
"""

# ruff: noqa: RUF001

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from datetime import time
from datetime import timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

from aivus_backend.email_agent import safety
from aivus_backend.email_agent.events import NotificationEvent
from aivus_backend.email_agent.events import dedup_window
from aivus_backend.email_agent.events import is_deferrable
from aivus_backend.email_agent.events import is_suppressible_by_mode
from aivus_backend.email_agent.models import NotificationChannel
from aivus_backend.email_agent.models import NotificationChannelType
from aivus_backend.email_agent.models import NotificationLog
from aivus_backend.email_agent.models import OutboundDraft
from aivus_backend.email_agent.models import OutboundDraftStatus
from aivus_backend.projects.brief_emails import resolve_vendor_email_language
from aivus_backend.users.tasks import send_to_recipient_email

if TYPE_CHECKING:
    from aivus_backend.users.models import Vendor

logger = logging.getLogger(__name__)

NOTIFICATION_MODE_EVERY = "every"
NOTIFICATION_MODE_URGENT_AND_DIGEST = "urgent_and_digest"

DIGEST_HOUR = 9
DIGEST_LEAD_WINDOW = timedelta(hours=24)
_FLUSH_BATCH = 100
_ALL_DAYS = (1, 2, 3, 4, 5, 6, 7)

_TITLES: dict[str, dict[str, str]] = {
    NotificationEvent.DRAFT_CREATED: {
        "en": "Draft ready for review",
        "ru": "Черновик готов к проверке",
    },
    NotificationEvent.DRAFT_OVERDUE: {
        "en": "Draft expired without a reply",
        "ru": "Черновик протух без ответа",
    },
    NotificationEvent.URGENT_LEAD: {
        "en": "Urgent lead needs you",
        "ru": "Срочный лид требует внимания",
    },
    NotificationEvent.ESCALATION: {
        "en": "Email needs your decision",
        "ru": "Письмо требует вашего решения",
    },
    NotificationEvent.MAILBOX_DISCONNECTED: {
        "en": "Mailbox disconnected",
        "ru": "Почтовый ящик отключился",
    },
    NotificationEvent.OOO_PAUSED: {
        "en": "Client is out of office",
        "ru": "Клиент в отпуске",
    },
    NotificationEvent.PROMISE_DUE: {
        "en": "Your promise needs attention",
        "ru": "Ваше обещание требует внимания",
    },
    NotificationEvent.INBOUND_EMAIL: {
        "en": "New client email",
        "ru": "Новое письмо клиента",
    },
    NotificationEvent.DAILY_DIGEST: {
        "en": "Your daily summary",
        "ru": "Дневная сводка",
    },
}

_INTROS: dict[str, dict[str, str]] = {
    NotificationEvent.DRAFT_CREATED: {
        "en": "The agent drafted a reply and is waiting for your approval.",
        "ru": "Агент подготовил ответ и ждет вашего одобрения.",
    },
    NotificationEvent.DRAFT_OVERDUE: {
        "en": "A first-reply draft expired before approval. The lead is waiting.",
        "ru": "Черновик первого ответа протух без одобрения. Лид ждет ответа.",
    },
    NotificationEvent.URGENT_LEAD: {
        "en": "A client email looks time-sensitive and may need a fast answer.",
        "ru": "Письмо клиента выглядит срочным и, возможно, требует быстрого ответа.",
    },
    NotificationEvent.ESCALATION: {
        "en": "The agent was not confident enough to answer and left it to you.",
        "ru": "Агент не был уверен в ответе и передал письмо вам.",
    },
    NotificationEvent.MAILBOX_DISCONNECTED: {
        "en": "The agent can no longer read this mailbox. Reconnect it to resume.",
        "ru": "Агент больше не может читать этот ящик. Переподключите его.",
    },
    NotificationEvent.OOO_PAUSED: {
        "en": "The client sent an out-of-office reply, so follow-ups are paused.",
        "ru": "Клиент прислал автоответ об отсутствии, напоминания приостановлены.",
    },
    NotificationEvent.PROMISE_DUE: {
        "en": "You promised the client something that is due now or already late.",
        "ru": "Вы обещали клиенту то, что уже пора сделать или что просрочено.",
    },
    NotificationEvent.INBOUND_EMAIL: {
        "en": "A new client email arrived.",
        "ru": "Пришло новое письмо от клиента.",
    },
    NotificationEvent.DAILY_DIGEST: {
        "en": "Here is what is waiting for you today.",
        "ru": "Вот что ждет вашего внимания сегодня.",
    },
}

_CTA_LABELS: dict[str, str] = {
    "en": "Open Aivus",
    "ru": "Открыть Aivus",
}

_DEFAULT_LANGUAGE = "en"


def _frontend_url() -> str:
    return getattr(settings, "FRONTEND_URL", "https://go.aivus.co").rstrip("/")


def _localized(table: dict[str, dict[str, str]], event: str, language: str) -> str:
    entry = table.get(event, {})
    return entry.get(language) or entry.get(_DEFAULT_LANGUAGE, "")


def resolve_notification_recipient(vendor: Vendor) -> str:
    profile = getattr(vendor, "agent_profile", None)
    if profile is not None and profile.producer_email:
        return profile.producer_email
    settings_row = getattr(vendor, "vendor_settings", None)
    if settings_row is not None and settings_row.lead_notification_email:
        return settings_row.lead_notification_email
    owner = getattr(vendor, "owner", None)
    return owner.email if owner is not None else ""


def _render_context(event: str, payload: dict, language: str) -> dict:
    return {
        "title": _localized(_TITLES, event, language),
        "intro": _localized(_INTROS, event, language),
        "lines": [
            safety.redact_for_notification(str(line))
            for line in payload.get("lines", [])
        ],
        "cta_url": payload.get("cta_url") or _frontend_url(),
        "cta_label": _CTA_LABELS.get(language, _CTA_LABELS[_DEFAULT_LANGUAGE]),
        "hint": payload.get("hint", ""),
        "frontend_url": _frontend_url(),
    }


def _template_for_event(event: str, language: str) -> str:
    name = (
        "agent_digest"
        if event == NotificationEvent.DAILY_DIGEST
        else "agent_notification"
    )
    return f"emails/{name}_{language}.html"


def _deliver_email(
    channel: NotificationChannel | None,
    vendor: Vendor,
    event: str,
    payload: dict,
    language: str,
) -> None:
    recipient = ""
    if channel is not None:
        recipient = channel.config.get("address", "")
    if not recipient:
        recipient = resolve_notification_recipient(vendor)
    if not recipient:
        msg = f"no notification recipient for vendor {vendor.id}"
        raise ValueError(msg)
    send_to_recipient_email.delay(
        recipient_email=recipient,
        template=_template_for_event(event, language),
        subject=_localized(_TITLES, event, language),
        context=_render_context(event, payload, language),
    )


ChannelHandler = Callable[
    [NotificationChannel | None, "Vendor", str, dict, str],
    None,
]

CHANNEL_HANDLERS: dict[str, ChannelHandler] = {
    NotificationChannelType.EMAIL: _deliver_email,
}


def _channels_for(vendor: Vendor) -> list[NotificationChannel | None]:
    channels: list[NotificationChannel | None] = list(
        vendor.notification_channels.filter(enabled=True)
    )
    return channels or [None]


def _deliver(
    channel: NotificationChannel | None,
    vendor: Vendor,
    event: str,
    payload: dict,
    language: str,
) -> tuple[bool, str]:
    channel_type = (
        channel.type if channel is not None else NotificationChannelType.EMAIL
    )
    handler = CHANNEL_HANDLERS.get(channel_type)
    if handler is None:
        return _fallback_to_email(
            vendor,
            event,
            payload,
            language,
            f"unsupported channel {channel_type}",
        )
    try:
        handler(channel, vendor, event, payload, language)
    except Exception as exc:
        logger.warning("notification channel %s failed: %s", channel_type, exc)
        if channel_type != NotificationChannelType.EMAIL:
            return _fallback_to_email(
                vendor,
                event,
                payload,
                language,
                f"{channel_type} failed: {exc}",
            )
        return False, str(exc)
    return True, ""


def _fallback_to_email(
    vendor: Vendor,
    event: str,
    payload: dict,
    language: str,
    reason: str,
) -> tuple[bool, str]:
    try:
        _deliver_email(None, vendor, event, payload, language)
    except Exception as exc:
        logger.warning("notification email fallback failed: %s", exc)
        return False, str(exc)
    return True, f"fallback to email: {reason}"


def notification_mode(vendor: Vendor) -> str:
    profile = getattr(vendor, "agent_profile", None)
    if profile is None:
        return NOTIFICATION_MODE_EVERY
    return profile.notification_rules.get("mode", NOTIFICATION_MODE_EVERY)


def _working_hours(vendor: Vendor) -> dict:
    profile = getattr(vendor, "agent_profile", None)
    return profile.working_hours if profile is not None else {}


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def is_within_working_hours(working_hours: dict, now: datetime) -> bool:
    """Whether ``now`` falls inside the vendor's working hours.

    An empty or malformed config means "no restriction" — the notification is
    sent immediately rather than dropped.
    """
    tz_name = working_hours.get("timezone")
    start = working_hours.get("start")
    end = working_hours.get("end")
    if not tz_name or not start or not end:
        return True
    try:
        local = now.astimezone(ZoneInfo(tz_name))
        start_time = _parse_hhmm(start)
        end_time = _parse_hhmm(end)
    except (ValueError, KeyError):
        return True
    days = working_hours.get("days") or _ALL_DAYS
    if local.isoweekday() not in days:
        return False
    return start_time <= local.time() < end_time


def next_window_start(working_hours: dict, now: datetime) -> datetime | None:
    """The next moment working hours open, or None when there is no restriction."""
    tz_name = working_hours.get("timezone")
    start = working_hours.get("start")
    if not tz_name or not start or not working_hours.get("end"):
        return None
    try:
        tz = ZoneInfo(tz_name)
        start_time = _parse_hhmm(start)
    except (ValueError, KeyError):
        return None
    days = working_hours.get("days") or _ALL_DAYS
    local = now.astimezone(tz)
    for offset in range(8):
        candidate = datetime.combine(
            (local + timedelta(days=offset)).date(), start_time, tzinfo=tz
        )
        if candidate.isoweekday() in days and candidate > local:
            return candidate
    return None


def is_duplicate(vendor: Vendor, event: str, dedup_key: str) -> bool:
    if not dedup_key:
        return False
    since = timezone.now() - dedup_window(event)
    return NotificationLog.objects.filter(
        vendor=vendor,
        event=event,
        dedup_key=dedup_key,
        created_at__gte=since,
    ).exists()


def _is_suppressed_by_mode(vendor: Vendor, event: str, *, urgent: bool) -> bool:
    if urgent or not is_suppressible_by_mode(event):
        return False
    return notification_mode(vendor) == NOTIFICATION_MODE_URGENT_AND_DIGEST


def notify(
    vendor: Vendor,
    event: str,
    payload: dict | None = None,
    *,
    urgent: bool = False,
    dedup_key: str = "",
) -> NotificationLog | None:
    payload = payload or {}
    if _is_suppressed_by_mode(vendor, event, urgent=urgent):
        return None
    if is_duplicate(vendor, event, dedup_key):
        return None

    if is_deferrable(event) and not urgent:
        now = timezone.now()
        working_hours = _working_hours(vendor)
        if not is_within_working_hours(working_hours, now):
            send_after = next_window_start(working_hours, now)
            if send_after is not None:
                return NotificationLog.objects.create(
                    vendor=vendor,
                    event=event,
                    payload=payload,
                    dedup_key=dedup_key,
                    delivered=False,
                    send_after=send_after,
                )

    language = resolve_vendor_email_language(vendor)
    result: NotificationLog | None = None
    for channel in _channels_for(vendor):
        delivered, error = _deliver(channel, vendor, event, payload, language)
        result = NotificationLog.objects.create(
            channel=channel,
            vendor=vendor,
            event=event,
            payload=payload,
            dedup_key=dedup_key,
            delivered=delivered,
            error=error,
        )
    return result


def flush_due_notifications(now: datetime) -> int:
    """Send deferred notifications whose working-hours window has opened."""
    due = list(
        NotificationLog.objects.filter(
            delivered=False,
            error="",
            send_after__isnull=False,
            send_after__lte=now,
        )[:_FLUSH_BATCH]
    )
    sent = 0
    for log in due:
        language = resolve_vendor_email_language(log.vendor)
        try:
            _deliver_email(log.channel, log.vendor, log.event, log.payload, language)
        except Exception as exc:
            log.error = str(exc)
            log.save(update_fields=["error"])
            continue
        log.delivered = True
        log.send_after = None
        log.save(update_fields=["delivered", "send_after"])
        sent += 1
    return sent


def is_digest_hour(working_hours: dict, now: datetime) -> bool:
    tz_name = working_hours.get("timezone")
    tz = ZoneInfo(tz_name) if tz_name else ZoneInfo("UTC")
    return now.astimezone(tz).hour == DIGEST_HOUR


def build_daily_digest(vendor: Vendor) -> dict | None:
    """Assemble the daily digest, or None when nothing is waiting."""
    now = timezone.now()
    pending_drafts = list(
        OutboundDraft.objects.filter(
            thread__vendor=vendor,
            status=OutboundDraftStatus.PENDING,
        ).select_related("thread")[:20]
    )
    fresh_leads = list(
        vendor.email_threads.filter(
            project__isnull=False,
            created_at__gte=now - DIGEST_LEAD_WINDOW,
        )[:20]
    )
    if not pending_drafts and not fresh_leads:
        return None

    lines: list[str] = []
    if pending_drafts:
        lines.append(f"Drafts awaiting approval: {len(pending_drafts)}")
        lines.extend(
            f"- {draft.thread.canonical_subject or '(no subject)'}"
            for draft in pending_drafts
        )
    if fresh_leads:
        lines.append(f"New leads in the last 24h: {len(fresh_leads)}")
        lines.extend(
            f"- {lead.canonical_subject or lead.client_email or '(no subject)'}"
            for lead in fresh_leads
        )
    return {"lines": lines}


def dispatch_due_digests(now: datetime) -> int:
    """Send the daily digest to each vendor whose local digest hour is now."""
    from aivus_backend.users.models import Vendor  # noqa: PLC0415

    vendors = Vendor.objects.filter(
        email_accounts__deleted_at__isnull=True,
        deleted_at__isnull=True,
    ).distinct()
    sent = 0
    for vendor in vendors:
        if not is_digest_hour(_working_hours(vendor), now):
            continue
        digest = build_daily_digest(vendor)
        if digest is None:
            continue
        log = notify(
            vendor,
            NotificationEvent.DAILY_DIGEST,
            digest,
            urgent=True,
            dedup_key=now.date().isoformat(),
        )
        if log is not None:
            sent += 1
    return sent
