"""Email dispatch for the personal-vendor-link send flow (Stage 2 S2-8).

Builds the client lead email (register CTA, public share link, PDF attachment)
and the vendor notification email. Account matching decides whether the client
is invited to register or to log in; the dispatch is uniform in timing so the
Send response cannot be used to enumerate which emails already have accounts.
"""

from __future__ import annotations

import base64
import logging
from urllib.parse import urlencode

from django.conf import settings

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


def _frontend_url() -> str:
    return getattr(settings, "FRONTEND_URL", "https://go.aivus.co").rstrip("/")


def _subject(table: dict, language: str) -> str:
    return table.get(language) or table["en"]


def resolve_email_language(brief, accept_language: str = "") -> str:
    return resolve_language(brief.document_language or None, accept_language or None)


def _client_register_url(brief, recipient_email: str, token: str) -> str:
    frontend = _frontend_url()
    params = {"email": recipient_email}
    if token:
        params["token"] = token
    return f"{frontend}/app/brief/claim/{brief.id}?{urlencode(params)}"


def _share_url(token: str) -> str:
    return f"{_frontend_url()}/shared-brief/{token}"


def _project_url(project) -> str:
    return f"{_frontend_url()}/app/dashboard/{project.id}"


def _brief_pdf_attachment(brief) -> tuple[str, str, str] | None:
    from aivus_backend.projects import brief_pdf  # noqa: PLC0415

    document = (
        brief.final_documents.filter(kind=FinalDocumentKind.PRODUCTION_BRIEF).first()
        or brief.final_documents.first()
    )
    if not document:
        return None
    try:
        pdf_bytes = brief_pdf.render_final_document_pdf(document)
    except Exception:
        logger.exception("brief pdf render failed for email: brief=%s", brief.id)
        return None
    label = brief_pdf.DOCUMENT_TITLE_BY_KIND.get(document.kind, "Brief")
    base_name = (brief.title or "Brief").strip()
    safe = "".join(c for c in base_name if c.isalnum() or c in " _-").strip()[:60]
    filename = f"{safe or 'Brief'} - {label}.pdf"
    return filename, base64.b64encode(pdf_bytes).decode("ascii"), "application/pdf"


def send_client_lead_email(
    brief,
    recipient_email: str,
    share_token: str,
    language: str,
) -> None:
    """Send the client their copy: register/login CTA, share link, PDF."""
    from aivus_backend.users.tasks import send_templated_email  # noqa: PLC0415
    from aivus_backend.users.tasks import send_to_recipient_email  # noqa: PLC0415

    existing = User.objects.filter(
        email__iexact=recipient_email, deleted_at__isnull=True
    ).first()
    template = f"emails/brief_sent_client_{language}.html"
    subject = _subject(CLIENT_SUBJECTS, language)
    context = {
        "vendor_name": brief_vendor_name(brief),
        "recipient_email": recipient_email,
        "register_url": _client_register_url(
            brief, recipient_email, brief.anonymous_token or ""
        ),
        "share_url": _share_url(share_token),
        "frontend_url": _frontend_url(),
        "is_existing_account": bool(existing),
    }
    attachments = [a for a in [_brief_pdf_attachment(brief)] if a]

    if existing:
        send_templated_email.delay(
            user_id=str(existing.id),
            template=template,
            subject=subject,
            context=context,
        )
    else:
        send_to_recipient_email.delay(
            recipient_email=recipient_email,
            template=template,
            subject=subject,
            context=context,
            attachments=attachments,
        )


def send_vendor_lead_email(project, brief, language: str) -> None:
    """Notify the vendor that a new lead landed. No PDF, leads to the cabinet."""
    from aivus_backend.users.tasks import send_to_recipient_email  # noqa: PLC0415

    recipient = _vendor_notification_recipient(project.vendor)
    if not recipient:
        logger.warning("no vendor notification recipient: vendor=%s", project.vendor_id)
        return
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
