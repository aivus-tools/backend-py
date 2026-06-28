"""Email dispatch for the personal-vendor-link send flow (Stage 2 S2-8).

Builds the client lead email (register CTA, public share link, PDF attachment)
and the vendor notification email. Account matching decides whether the client
is invited to register or to log in; the dispatch is uniform in timing so the
Send response cannot be used to enumerate which emails already have accounts.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from urllib.parse import urlencode

from django.conf import settings
from django.core.cache import cache

from aivus_backend.core.enums import CLIENT_FACING_DOCUMENT_KINDS
from aivus_backend.core.enums import FinalDocumentKind
from aivus_backend.users.i18n import resolve_language
from aivus_backend.users.models import User

logger = logging.getLogger(__name__)

CLIENT_SUBJECTS = {
    "en": "Your brief is ready",
    "ru": "Ваш бриф готов",
}
VENDOR_SUBJECTS = {
    "en": "New brief via your personal link",
    "ru": "Новый бриф через вашу персональную ссылку",
}

# The anonymous Send flow lets a visitor put any recipient address on a branded
# "Your brief is ready" email with a PDF attachment. The per-IP Send limit alone
# does not stop a bot pool from bombing one victim address with branded mail, so
# the dispatch is throttled per recipient regardless of which brief or IP it came
# from: at most CLIENT_LEAD_EMAIL_PER_RECIPIENT_MAX branded emails per recipient
# per window, and the same brief is never re-sent to the same address.
CLIENT_LEAD_EMAIL_PER_RECIPIENT_WINDOW_SECONDS = 3600
CLIENT_LEAD_EMAIL_PER_RECIPIENT_MAX = 5
CLIENT_LEAD_EMAIL_DEDUP_WINDOW_SECONDS = 86400


def _recipient_cache_key(recipient_email: str) -> str:
    digest = hashlib.sha256(recipient_email.strip().lower().encode("utf-8")).hexdigest()
    return f"client_lead_email:recipient:{digest}"


def _dedup_cache_key(recipient_email: str, brief_id) -> str:
    digest = hashlib.sha256(recipient_email.strip().lower().encode("utf-8")).hexdigest()
    return f"client_lead_email:dedup:{brief_id}:{digest}"


def _client_lead_email_allowed(recipient_email: str, brief_id) -> bool:
    """Throttle and de-duplicate branded client lead emails per recipient.

    Returns False when the recipient has already received this brief or has hit
    the per-recipient send ceiling within the window, so an attacker cannot bomb
    a victim address from a pool of IPs.
    """
    dedup_key = _dedup_cache_key(recipient_email, brief_id)
    if not cache.add(dedup_key, 1, CLIENT_LEAD_EMAIL_DEDUP_WINDOW_SECONDS):
        logger.info("client lead email deduplicated: brief=%s", brief_id)
        return False

    recipient_key = _recipient_cache_key(recipient_email)
    cache.add(recipient_key, 0, CLIENT_LEAD_EMAIL_PER_RECIPIENT_WINDOW_SECONDS)
    try:
        sent_count = cache.incr(recipient_key)
    except ValueError:
        cache.set(recipient_key, 1, CLIENT_LEAD_EMAIL_PER_RECIPIENT_WINDOW_SECONDS)
        sent_count = 1
    if sent_count > CLIENT_LEAD_EMAIL_PER_RECIPIENT_MAX:
        logger.warning("client lead email throttled for recipient: brief=%s", brief_id)
        cache.delete(dedup_key)
        return False
    return True


def _frontend_url() -> str:
    return getattr(settings, "FRONTEND_URL", "https://go.aivus.co").rstrip("/")


def _subject(table: dict, language: str) -> str:
    return table.get(language) or table["en"]


def resolve_email_language(brief, accept_language: str = "") -> str:
    return resolve_language(brief.document_language or None, accept_language or None)


def resolve_vendor_email_language(vendor) -> str:
    """Language for the vendor notification, driven by the vendor's own settings.

    The brief's ``document_language`` reflects the client's choice and is empty
    for inbound leads (webhook / wix), so it must not drive the vendor's email.
    Use the owner's ``UserSettings.language``, defaulting to English.
    """
    from aivus_backend.users.i18n import user_language  # noqa: PLC0415

    owner = getattr(vendor, "owner", None)
    if owner is None:
        return "en"
    return user_language(owner)


def _client_register_url(brief, recipient_email: str, token: str) -> str:
    frontend = _frontend_url()
    params = {"email": recipient_email}
    if token:
        params["token"] = token
    return f"{frontend}/app/brief/claim/{brief.id}?{urlencode(params)}"


def _share_url(token: str) -> str:
    return f"{_frontend_url()}/shared-brief/{token}"


def _project_url(project) -> str:
    return f"{_frontend_url()}/app/dashboard/{project.id}/details"


def _brief_pdf_attachment(brief) -> tuple[str, str, str] | None:
    from aivus_backend.projects import brief_pdf  # noqa: PLC0415

    # The client-facing email must never attach the vendor outreach email
    # (kind=vendor_email) — it carries the vendor's PII and is owner-only (PRD §5).
    # Prefer the production brief, and fall back only within the client-facing
    # kinds; a bare .first() would surface vendor_email for a brief that somehow
    # has only that document, because the kind ordering sorts it last.
    document = (
        brief.final_documents.filter(kind=FinalDocumentKind.PRODUCTION_BRIEF).first()
        or brief.final_documents.filter(kind__in=CLIENT_FACING_DOCUMENT_KINDS).first()
    )
    if not document:
        return None
    try:
        pdf_bytes = brief_pdf.render_final_document_pdf(document)
    except Exception:
        logger.exception("brief pdf render failed for email: brief=%s", brief.id)
        return None
    label = brief_pdf.document_title_for(document.kind, brief.document_language)
    base_name = (brief.title or "Brief").strip()
    safe = "".join(c for c in base_name if c.isalnum() or c in " _-").strip()[:60]
    filename = f"{safe or 'Brief'} - {label}.pdf"
    return filename, base64.b64encode(pdf_bytes).decode("ascii"), "application/pdf"


def send_client_lead_email(
    brief,
    recipient_email: str,
    share_token: str,
    language: str,
    project=None,
) -> None:
    """Send the client their copy: register/login CTA, share link, PDF.

    The PDF is attached in both branches (new lead and existing account) so the
    recipient always gets a downloadable copy; only the CTA text differs. We
    deliver through the bare-address task because the client template carries no
    {user} context, and that task is the only path that supports attachments.
    The project is passed so the email names the actual vendor rather than the
    generic "your agency".
    """
    from aivus_backend.users.tasks import send_to_recipient_email  # noqa: PLC0415

    if not _client_lead_email_allowed(recipient_email, brief.id):
        return

    existing = User.objects.filter(
        email__iexact=recipient_email, deleted_at__isnull=True
    ).first()
    template = f"emails/brief_sent_client_{language}.html"
    subject = _subject(CLIENT_SUBJECTS, language)
    context = {
        "vendor_name": brief_vendor_name(brief, project=project),
        "recipient_email": recipient_email,
        "register_url": _client_register_url(
            brief, recipient_email, brief.anonymous_token or ""
        ),
        "share_url": _share_url(share_token),
        "frontend_url": _frontend_url(),
        "is_existing_account": bool(existing),
    }
    attachments = [a for a in [_brief_pdf_attachment(brief)] if a]

    # BE-3: the dedup key was claimed in _client_lead_email_allowed BEFORE this
    # enqueue. If .delay() fails (broker hiccup), the email never goes out yet the
    # key would block any resend for the full 24h dedup window. Release the key on
    # enqueue failure (as the per-recipient ceiling path already does) so a manual
    # retry can re-send, then re-raise for the caller to log.
    try:
        send_to_recipient_email.delay(
            recipient_email=existing.email if existing else recipient_email,
            template=template,
            subject=subject,
            context=context,
            attachments=attachments,
        )
    except Exception:
        cache.delete(_dedup_cache_key(recipient_email, brief.id))
        raise


def send_vendor_lead_email(project, brief) -> None:
    """Notify the vendor that a new lead landed. No PDF, leads to the cabinet.

    The language follows the vendor's own settings, not the brief's
    document_language: the latter is the client's choice and is empty for inbound
    leads (webhook / wix), which would otherwise force the vendor email to English.
    """
    from aivus_backend.users.tasks import send_to_recipient_email  # noqa: PLC0415

    recipient = _vendor_notification_recipient(project.vendor)
    if not recipient:
        logger.warning("no vendor notification recipient: vendor=%s", project.vendor_id)
        return
    language = resolve_vendor_email_language(project.vendor)
    context = {
        "vendor_name": brief_vendor_name(brief, project=project),
        "contact_email": brief.contact_email,
        "project_url": _project_url(project),
        "frontend_url": _frontend_url(),
    }
    send_to_recipient_email.delay(
        recipient_email=recipient,
        template=f"emails/vendor_lead_{language}.html",
        subject=_subject(VENDOR_SUBJECTS, language),
        context=context,
    )


def _vendor_notification_recipient(vendor) -> str:
    settings_row = getattr(vendor, "vendor_settings", None)
    if settings_row and settings_row.lead_notification_email:
        return settings_row.lead_notification_email
    owner = vendor.owner
    return owner.email if owner else ""


def brief_vendor_name(brief, project=None) -> str:
    vendor = project.vendor if project else None
    if vendor is None:
        return "your agency"
    settings_row = getattr(vendor, "vendor_settings", None)
    if settings_row:
        name = (settings_row.company_name or "").strip() or (
            settings_row.agency_name or ""
        ).strip()
        if name:
            return name
    return vendor.name
