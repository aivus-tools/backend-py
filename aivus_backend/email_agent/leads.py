"""Lead-seam service for inbound email (Stage 3).

Wraps the shared inbound-brief creation so a new email order becomes a canonical
lead (Brief + Project) on the same seam as Stage 2, but with source ``email``:
the brief-chat first-reply task is never enqueued, because the Stage 3 email
agent owns the first response. The created Project is linked to its thread so a
brief filled in via the agent's link attaches to this lead instead of spawning a
duplicate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aivus_backend.core.enums import BriefSource
from aivus_backend.projects.api.views_brief_v3 import _create_inbound_brief
from aivus_backend.projects.models import Project

if TYPE_CHECKING:
    from aivus_backend.email_agent.models import EmailThread
    from aivus_backend.projects.models import Brief
    from aivus_backend.users.models import Vendor


def create_email_lead(
    *,
    vendor: Vendor,
    message: str,
    contact_email: str = "",
    contact_name: str = "",
    thread: EmailThread | None = None,
) -> tuple[Brief, Project | None]:
    """Create a canonical lead from an inbound email order.

    Returns the created ``Brief`` and its vendor ``Project`` (if a vendor was
    given). Does not enqueue the brief-chat first-reply task.
    """
    brief, _task_id, _token = _create_inbound_brief(
        message=message,
        contact_email=contact_email,
        contact_name=contact_name,
        source=BriefSource.EMAIL,
        vendor=vendor,
    )

    project = (
        Project.objects.filter(vendor=vendor, brief=brief).first()
        if vendor is not None
        else None
    )
    if thread is not None and project is not None:
        thread.project = project
        thread.save(update_fields=["project", "updated_at"])

    return brief, project
