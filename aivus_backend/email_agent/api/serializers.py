"""Email-agent API serializers (functions returning dict)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aivus_backend.email_agent.models import EmailAccount


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
