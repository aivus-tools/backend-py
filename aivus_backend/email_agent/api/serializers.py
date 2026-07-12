"""Email-agent API serializers (functions returning dict)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aivus_backend.email_agent.models import EmailAccount
    from aivus_backend.email_agent.models import OutboundDraft


def serialize_account(account: EmailAccount) -> dict:
    """Public view of a connected mailbox (never exposes the credential)."""
    return {
        "id": str(account.id),
        "role": account.role,
        "email": account.email,
        "provider": account.provider,
        "status": account.status,
        "lastSyncedAt": (
            account.last_synced_at.isoformat() if account.last_synced_at else None
        ),
    }


def serialize_draft(draft: OutboundDraft) -> dict:
    """Vendor-facing view of an outbound draft awaiting review."""
    metadata = draft.metadata or {}
    return {
        "id": str(draft.id),
        "threadId": str(draft.thread_id),
        "kind": draft.kind,
        "status": draft.status,
        "body": draft.body,
        "variant": metadata.get("variant", ""),
        "action": metadata.get("action", ""),
        "edited": bool(metadata.get("edited", False)),
        "overdue": bool(metadata.get("overdue", False)),
        "expiresAt": draft.expires_at.isoformat() if draft.expires_at else None,
        "createdAt": draft.created_at.isoformat(),
    }
