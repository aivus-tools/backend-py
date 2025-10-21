"""Base managers for the project."""

from django.db import models
from django.utils import timezone


class JournalizeQuerySet(models.QuerySet):
    """QuerySet that filters out soft-deleted records by default."""

    def delete(self):
        """Soft delete all records in the queryset."""
        return self.update(deleted_at=timezone.now())

    def hard_delete(self):
        """Permanently delete all records in the queryset."""
        return super().delete()

    def alive(self):
        """Return only non-deleted records."""
        return self.filter(deleted_at__isnull=True)

    def deleted(self):
        """Return only deleted records."""
        return self.filter(deleted_at__isnull=False)


class JournalizeManager(models.Manager):
    """Manager that excludes soft-deleted records by default."""

    def get_queryset(self):
        """Return queryset excluding soft-deleted records."""
        return JournalizeQuerySet(self.model, using=self._db).filter(deleted_at__isnull=True)

    def all_with_deleted(self):
        """Return all records including soft-deleted ones."""
        return JournalizeQuerySet(self.model, using=self._db)

    def deleted_only(self):
        """Return only soft-deleted records."""
        return JournalizeQuerySet(self.model, using=self._db).filter(deleted_at__isnull=False)

