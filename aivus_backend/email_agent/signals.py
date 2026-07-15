"""Model signal handlers for the email agent (blob cleanup on delete).

Django's ``FileField.delete()`` is not called on cascade deletion, so a plain
``EmailMessage.delete()`` or ``EmailThread.delete()`` orphans the underlying GCS
object storage. A ``post_delete`` receiver on ``EmailAttachment`` runs on every
row deletion (direct or cascaded), so the blob is removed once the row is gone.
"""

from __future__ import annotations

import contextlib
import logging

from django.db.models.signals import post_delete
from django.dispatch import receiver

from aivus_backend.email_agent.models import EmailAttachment

logger = logging.getLogger(__name__)


@receiver(post_delete, sender=EmailAttachment)
def _delete_attachment_blob(sender, instance: EmailAttachment, **kwargs) -> None:
    """Remove the underlying blob when an ``EmailAttachment`` row is deleted."""
    file_field = getattr(instance, "file", None)
    if not file_field or not getattr(file_field, "name", ""):
        return
    with contextlib.suppress(Exception):
        file_field.delete(save=False)
