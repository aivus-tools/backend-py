"""Inbound attachment storage for the email agent (Stage 3, S3-12).

Attachments are stored on receipt, anchored to the message and thread, even when
the thread has no lead brief yet — so a client who mails a brief PDF before the
agent has drafted anything does not lose it. The declared MIME type is not
trusted: the bytes are sniffed and checked against the same allowlist and size
cap the brief pipeline uses, so a renamed executable never lands in storage.
When the thread becomes a lead, ``link_thread_attachments`` attaches them to the
brief.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.core.files.base import ContentFile

from aivus_backend.email_agent.models import EmailAttachment
from aivus_backend.projects.attachments import ALLOWED_MIME_TYPES
from aivus_backend.projects.attachments import DOCX_DETECTED_ALIASES
from aivus_backend.projects.attachments import DOCX_MIME
from aivus_backend.projects.attachments import MAX_ATTACHMENT_SIZE_BYTES
from aivus_backend.projects.attachments import sniff_mime_bytes

if TYPE_CHECKING:
    from aivus_backend.email_agent.models import EmailMessage
    from aivus_backend.projects.models import Brief

logger = logging.getLogger(__name__)

_FILENAME_MAX = 255


def _resolve_mime(declared: str, payload: bytes) -> str | None:
    """The trusted MIME type, or None if the file is not an allowed type.

    The sniffed type wins over the declared one. A declared docx is accepted on
    the usual zip/octet-stream sniff aliases, since libmagic reports the zip
    container inconsistently.
    """
    sniffed = sniff_mime_bytes(payload)
    if sniffed in ALLOWED_MIME_TYPES:
        return sniffed
    if declared == DOCX_MIME and sniffed in DOCX_DETECTED_ALIASES:
        return DOCX_MIME
    return None


def store_attachments(message: EmailMessage, parsed_attachments: list[dict]) -> int:
    """Persist the valid attachments of one inbound message. Returns the count.

    Invalid MIME types and oversized or empty payloads are dropped silently — an
    attachment the agent cannot use is not a reason to fail ingestion.
    """
    stored = 0
    for raw in parsed_attachments or []:
        payload = raw.get("payload")
        if not isinstance(payload, bytes) or not payload:
            continue
        if len(payload) > MAX_ATTACHMENT_SIZE_BYTES:
            continue
        declared = str(raw.get("content_type", "")).strip()
        mime = _resolve_mime(declared, payload)
        if mime is None:
            continue
        filename = str(raw.get("filename") or "attachment")[:_FILENAME_MAX]
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
