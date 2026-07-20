"""Inbound attachment storage for the email agent (Stage 3, S3-12).

Attachments are stored on receipt, anchored to the message and thread, even when
the thread has no lead brief yet — so a client who mails a brief PDF before the
agent has drafted anything does not lose it. The declared MIME type is not
trusted: the bytes are sniffed against an email-agent-specific allowlist that
covers what a video production vendor actually receives (references, edit
notes, spreadsheets, archives), and everything else is dropped and logged so
the vendor sees in the thread timeline that something arrived and did not make
it in. When the thread becomes a lead, ``link_thread_attachments`` attaches the
stored files to the brief.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.core.files.base import ContentFile

from aivus_backend.email_agent.models import AgentLog
from aivus_backend.email_agent.models import EmailAttachment
from aivus_backend.projects.attachments import DOCX_DETECTED_ALIASES
from aivus_backend.projects.attachments import DOCX_MIME
from aivus_backend.projects.attachments import MAX_ATTACHMENT_SIZE_BYTES
from aivus_backend.projects.attachments import sniff_mime_bytes

if TYPE_CHECKING:
    from aivus_backend.email_agent.models import EmailMessage
    from aivus_backend.projects.models import Brief

logger = logging.getLogger(__name__)

_FILENAME_MAX = 255

# Video production briefs come with references (mp4/mov/wav), edit notes (docx,
# pdf, txt), estimates (xlsx), decks (pptx) and asset bundles (zip). The brief
# pipeline's own allowlist is narrower (only what the LLM can read directly),
# so we do not reuse it here — the agent stores the file for the vendor to see,
# rendering is not required.
EMAIL_ALLOWED_MIME_TYPES = {
    "application/pdf",
    "application/zip",
    "application/x-zip-compressed",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    DOCX_MIME,
    "text/plain",
    "text/csv",
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "image/heic",
    "image/tiff",
    "video/mp4",
    "video/quicktime",
    "video/x-msvideo",
    "video/x-matroska",
    "video/webm",
    "audio/mpeg",
    "audio/mp4",
    "audio/wav",
    "audio/x-wav",
    "audio/ogg",
    "audio/webm",
}

_DROP_REASON_EMPTY = "empty_payload"
_DROP_REASON_OVERSIZED = "oversized"
_DROP_REASON_DISALLOWED_MIME = "disallowed_mime"


def _resolve_mime(declared: str, payload: bytes) -> str | None:
    """The trusted MIME type, or None if the file is not an allowed type.

    The sniffed type wins over the declared one. A declared docx is accepted on
    the usual zip/octet-stream sniff aliases, since libmagic reports the zip
    container inconsistently.
    """
    sniffed = sniff_mime_bytes(payload)
    if sniffed in EMAIL_ALLOWED_MIME_TYPES:
        return sniffed
    if declared == DOCX_MIME and sniffed in DOCX_DETECTED_ALIASES:
        return DOCX_MIME
    return None


def _log_drop(message: EmailMessage, filename: str, declared: str, reason: str) -> None:
    """Record a dropped attachment so the vendor sees it in the thread timeline."""
    logger.warning(
        "email_agent attachment dropped: message=%s filename=%r declared=%r reason=%s",
        message.id,
        filename,
        declared,
        reason,
    )
    AgentLog.objects.create(
        thread=message.thread,
        project=message.thread.project,
        event="attachment_dropped",
        payload={
            "message_id": str(message.id),
            "filename": filename,
            "declared_type": declared,
            "reason": reason,
        },
    )


def store_attachments(message: EmailMessage, parsed_attachments: list[dict]) -> int:
    """Persist the valid attachments of one inbound message. Returns the count.

    Attachments the agent cannot use are dropped (not a reason to fail ingestion)
    but logged: a silent drop means the vendor never learns that a client-sent
    file did not land, which is worse than any specific rejection.
    """
    stored = 0
    for raw in parsed_attachments or []:
        filename = str(raw.get("filename") or "attachment")[:_FILENAME_MAX]
        declared = str(raw.get("content_type", "")).strip()
        payload = raw.get("payload")
        if not isinstance(payload, bytes) or not payload:
            _log_drop(message, filename, declared, _DROP_REASON_EMPTY)
            continue
        if len(payload) > MAX_ATTACHMENT_SIZE_BYTES:
            _log_drop(message, filename, declared, _DROP_REASON_OVERSIZED)
            continue
        mime = _resolve_mime(declared, payload)
        if mime is None:
            _log_drop(message, filename, declared, _DROP_REASON_DISALLOWED_MIME)
            continue
        attachment = EmailAttachment(
            message=message,
            thread=message.thread,
            filename=filename,
            mime_type=mime,
            size_bytes=len(payload),
        )
        attachment.file.save(filename, ContentFile(payload), save=False)
        attachment.save()
        stored += 1
    return stored


def link_thread_attachments(thread_id, brief: Brief) -> int:
    """Attach a thread's not-yet-linked attachments to its new lead brief."""
    return EmailAttachment.objects.filter(
        thread_id=thread_id, brief__isnull=True
    ).update(brief=brief)
