"""Base models for the project."""

import uuid

from django.db import models
from django.utils import timezone

from .managers import JournalizeManager


class JournalizeModel(models.Model):
    """
    Abstract base model with UUID primary key and soft delete functionality.

    All models inheriting from this will have:
    - UUID as primary key instead of integer
    - created_at: timestamp when record was created
    - updated_at: timestamp when record was last updated
    - deleted_at: timestamp when record was soft-deleted (null if active)

    Soft delete is implemented via the delete() method which sets deleted_at
    instead of removing the record from the database.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(null=True, auto_now_add=True)
    updated_at = models.DateTimeField(null=True, auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = JournalizeManager()

    class Meta:
        abstract = True

    def delete(self, using=None, keep_parents=False):  # noqa: FBT002
        """Soft delete the record by setting deleted_at timestamp."""
        self.deleted_at = timezone.now()
        self.save(update_fields=("deleted_at",))

    def hard_delete(self, using=None, keep_parents=False):  # noqa: FBT002
        """Permanently delete the record from the database."""
        super().delete(using=using, keep_parents=keep_parents)

    def restore(self):
        """Restore a soft-deleted record."""
        self.deleted_at = None
        self.save(update_fields=("deleted_at",))

    @property
    def is_deleted(self):
        """Check if the record is soft-deleted."""
        return self.deleted_at is not None
