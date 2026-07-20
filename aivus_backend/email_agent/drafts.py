"""Draft-only autonomy layer and OutboundDraft lifecycle (Stage 3, S3-20/21).

MVP is draft-only: the reply engine hands off a proposal, this layer queues it as
an OutboundDraft awaiting human review (PENDING == awaiting review), and a human
approves the actual send over the vendor's SMTP. ``auto_safe`` is a seam only and
never enables an automatic send (owner decision: no auto-send in MVP).

Lifecycle: approve (send + SENT, or EDITED_SENT via metadata), edit, reject
(DISCARDED), expire. A stale first-reply is never dropped silently — it expires,
gets flagged overdue, and re-notifies the producer so the lead is not lost.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.utils import timezone

from aivus_backend.email_agent import notifications
from aivus_backend.email_agent import reply
from aivus_backend.email_agent import sender
from aivus_backend.email_agent.events import NotificationEvent
from aivus_backend.email_agent.models import AgentLog
from aivus_backend.email_agent.models import EmailAccount
from aivus_backend.email_agent.models import EmailAccountRole
from aivus_backend.email_agent.models import EmailAccountStatus
from aivus_backend.email_agent.models import OutboundDraft
from aivus_backend.email_agent.models import OutboundDraftKind
from aivus_backend.email_agent.models import OutboundDraftStatus
from aivus_backend.email_agent.models import ThreadState
from aivus_backend.email_agent.models import VendorAgentProfile

if TYPE_CHECKING:
    from datetime import datetime

    from aivus_backend.email_agent.models import EmailMessage

_EXPIRE_BATCH = 100


class DraftError(Exception):
    """A draft action was refused (not pending, expired, or no agent mailbox)."""


def is_auto_send_enabled(profile: VendorAgentProfile | None) -> bool:
    """Whether safe replies may be sent without human approval.

    Always False in the MVP: ``auto_safe`` is reserved as a seam but never
    activates an automatic send. A human always approves the client-facing reply.
    """
    return False


def _require_pending(draft: OutboundDraft) -> None:
    if draft.status != OutboundDraftStatus.PENDING:
        msg = "draft is not pending"
        raise DraftError(msg)


def _mark_metadata(draft: OutboundDraft, key: str) -> dict:
    return {**(draft.metadata or {}), key: True}


def approve_draft(
    draft: OutboundDraft, *, edited_body: str | None = None
) -> EmailMessage:
    """Send an approved draft over the vendor's agent mailbox.

    A non-pending or expired draft is refused so a stale reply never reaches the
    client. ``edited_body`` marks the send as edited-sent.
    """
    _require_pending(draft)
    if draft.expires_at is not None and draft.expires_at <= timezone.now():
        msg = "draft has expired"
        raise DraftError(msg)

    thread = draft.thread
    vendor = thread.vendor
    agent = EmailAccount.objects.filter(
        vendor=vendor,
        role=EmailAccountRole.AGENT,
        status=EmailAccountStatus.CONNECTED,
        deleted_at__isnull=True,
    ).first()
    if agent is None:
        msg = "no connected agent mailbox"
        raise DraftError(msg)

    profile = VendorAgentProfile.objects.filter(vendor=vendor).first()
    producer_email = profile.producer_email if profile is not None else ""
    body = draft.body if edited_body is None else edited_body
    brief_link = reply.build_brief_link(thread)
    allowed_urls = (brief_link,) if brief_link else ()

    sent = sender.send_reply(
        agent,
        thread,
        body,
        producer_email=producer_email,
        parent=draft.in_reply_to_message,
        allowed_urls=allowed_urls,
    )

    draft.body = body
    draft.status = OutboundDraftStatus.SENT
    draft.provider_draft_id = sent.provider_message_id
    fields = ["body", "status", "provider_draft_id", "updated_at"]
    if edited_body is not None:
        draft.metadata = _mark_metadata(draft, "edited")
        fields.append("metadata")
    draft.save(update_fields=fields)

    if thread.state != ThreadState.HUMAN_TAKEOVER:
        thread.state = ThreadState.ENGAGED
        thread.save(update_fields=["state", "updated_at"])

    AgentLog.objects.create(
        thread=thread,
        project=thread.project,
        event="draft_sent",
        payload={"edited": edited_body is not None},
    )
    return sent


def edit_draft(draft: OutboundDraft, body: str) -> OutboundDraft:
    """Update a pending draft's body without sending it."""
    _require_pending(draft)
    draft.body = body
    draft.metadata = _mark_metadata(draft, "edited")
    draft.save(update_fields=["body", "metadata", "updated_at"])
    return draft


def reject_draft(draft: OutboundDraft) -> OutboundDraft:
    """Discard a pending draft so it is never sent."""
    _require_pending(draft)
    draft.status = OutboundDraftStatus.REJECTED
    draft.save(update_fields=["status", "updated_at"])
    AgentLog.objects.create(
        thread=draft.thread,
        project=draft.thread.project,
        event="draft_rejected",
        payload={},
    )
    return draft


def expire_stale_drafts(now: datetime) -> int:
    """Expire pending drafts past their deadline; re-notify on overdue first-reply.

    A stale first-reply draft is flagged overdue and the producer is re-notified
    so the lead is not lost silently; the flag feeds the follow-up dashboard.
    """
    stale = list(
        OutboundDraft.objects.filter(
            status=OutboundDraftStatus.PENDING,
            expires_at__isnull=False,
            expires_at__lte=now,
        ).select_related("thread", "thread__vendor")[:_EXPIRE_BATCH]
    )
    expired = 0
    for draft in stale:
        draft.status = OutboundDraftStatus.EXPIRED
        fields = ["status", "updated_at"]
        is_first_reply = draft.kind == OutboundDraftKind.FIRST_REPLY
        if is_first_reply:
            draft.metadata = _mark_metadata(draft, "overdue")
            fields.append("metadata")
        draft.save(update_fields=fields)

        if is_first_reply:
            notifications.notify(
                draft.thread.vendor,
                NotificationEvent.DRAFT_OVERDUE,
                {"lines": [f"Subject: {draft.thread.canonical_subject}"]},
                urgent=True,
                dedup_key=str(draft.id),
            )
        expired += 1
    return expired
