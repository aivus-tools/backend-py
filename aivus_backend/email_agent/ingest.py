"""Ingestion service: stitch a parsed message into a thread and store it.

Kept out of the Celery task so the logic is unit-testable against the DB.
Threads are reconstructed from Message-ID / References (there is no Gmail
threadId over IMAP), with a subject+sender fallback. Messages dedupe on
(account, provider_message_id) where provider_message_id is the RFC Message-ID.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from aivus_backend.email_agent.models import EmailDirection
from aivus_backend.email_agent.models import EmailMessage
from aivus_backend.email_agent.models import EmailThread

if TYPE_CHECKING:
    from aivus_backend.email_agent.models import EmailAccount

_MESSAGE_ID_RE = re.compile(r"<[^>]+>")


def _referenced_ids(parsed: dict) -> list[str]:
    raw = f"{parsed.get('in_reply_to', '')} {parsed.get('references', '')}"
    return _MESSAGE_ID_RE.findall(raw)


def _participants(parsed: dict) -> list[str]:
    seen: list[str] = []
    for address in (
        parsed.get("from_email", ""),
        *parsed.get("to_emails", []),
        *parsed.get("cc_emails", []),
    ):
        normalized = (address or "").strip()
        if normalized and normalized not in seen:
            seen.append(normalized)
    return seen


def _merge_participants(thread: EmailThread, parsed: dict) -> None:
    current = list(thread.participants or [])
    changed = False
    for address in _participants(parsed):
        if address not in current:
            current.append(address)
            changed = True
    if changed:
        thread.participants = current
        thread.save(update_fields=["participants", "updated_at"])


def stitch_thread(account: EmailAccount, parsed: dict) -> EmailThread:
    """Find the thread this message belongs to, or create a new one."""
    vendor = account.vendor

    referenced = _referenced_ids(parsed)
    if referenced:
        anchor = (
            EmailMessage.objects.filter(
                thread__vendor=vendor,
                message_id_header__in=referenced,
            )
            .select_related("thread")
            .first()
        )
        if anchor is not None:
            _merge_participants(anchor.thread, parsed)
            return anchor.thread

    canonical = parsed.get("canonical_subject", "")
    client_email = parsed.get("from_email", "")
    if canonical and client_email:
        match = (
            EmailThread.objects.filter(
                vendor=vendor,
                canonical_subject=canonical,
                client_email=client_email,
                deleted_at__isnull=True,
            )
            .order_by("-updated_at")
            .first()
        )
        if match is not None:
            _merge_participants(match, parsed)
            return match

    return EmailThread.objects.create(
        vendor=vendor,
        provider_thread_id=_thread_id(parsed),
        client_email=client_email,
        client_name=parsed.get("from_name", ""),
        canonical_subject=canonical,
        participants=_participants(parsed),
    )


def _thread_id(parsed: dict) -> str:
    referenced = _referenced_ids(parsed)
    root = referenced[0] if referenced else parsed.get("message_id_header", "")
    return (root or "").strip("<>") or parsed.get("message_id_header", "") or "thread"


def store_inbound_message(
    account: EmailAccount,
    thread: EmailThread,
    parsed: dict,
    uid: int,
) -> EmailMessage | None:
    """Store an inbound message, deduped per account. Returns None on duplicate."""
    provider_message_id = parsed.get("message_id_header") or f"uid-{uid}"
    _obj, created = EmailMessage.objects.get_or_create(
        account=account,
        provider_message_id=provider_message_id,
        defaults={
            "thread": thread,
            "direction": EmailDirection.IN,
            "from_email": parsed.get("from_email", ""),
            "to_emails": parsed.get("to_emails", []),
            "cc_emails": parsed.get("cc_emails", []),
            "subject": parsed.get("subject", ""),
            "body_clean": parsed.get("body_clean", ""),
            "headers": parsed.get("headers", {}),
            "message_id_header": parsed.get("message_id_header", ""),
            "in_reply_to": parsed.get("in_reply_to", ""),
            "references": parsed.get("references", ""),
        },
    )
    return _obj if created else None


def ingest_parsed(
    account: EmailAccount,
    parsed: dict,
    uid: int,
) -> EmailMessage | None:
    """Stitch the message into its thread and store it. None if it was a dup."""
    thread = stitch_thread(account, parsed)
    return store_inbound_message(account, thread, parsed, uid)
