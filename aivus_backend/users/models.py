import uuid
from typing import ClassVar

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .managers import UserManager


class User(AbstractUser):
    """
    Custom user model for Aivus-Backend with UUID primary key and soft delete.

    Features:
    - UUID as primary key instead of integer for security
    - Email-based authentication (no username)
    - Soft delete functionality (deleted_at field)
    - Timestamps for creation and updates

    If adding fields that need to be filled at user signup,
    check forms.SignupForm and forms.SocialSignupForms accordingly.
    """

    # Override id with UUID
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Soft delete and timestamps
    created_at = models.DateTimeField(null=True, auto_now_add=True)
    updated_at = models.DateTimeField(null=True, auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    # User fields
    name = models.CharField(_("Name of User"), blank=True, max_length=255)
    first_name = None  # type: ignore[assignment]
    last_name = None  # type: ignore[assignment]
    email = models.EmailField(_("email address"), unique=True)
    username = None  # type: ignore[assignment]

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects: ClassVar[UserManager] = UserManager()

    def get_absolute_url(self) -> str:
        """Get URL for user's detail view.

        Returns:
            str: URL for user detail.

        """
        return reverse("users:detail", kwargs={"pk": self.id})

    def delete(self, using=None, keep_parents=False):  # noqa: FBT002
        """Soft delete the user by setting deleted_at timestamp."""
        self.deleted_at = timezone.now()
        self.save(update_fields=("deleted_at",))

    def hard_delete(self, using=None, keep_parents=False):  # noqa: FBT002
        """Permanently delete the user from the database."""
        super().delete(using=using, keep_parents=keep_parents)

    def restore(self):
        """Restore a soft-deleted user."""
        self.deleted_at = None
        self.save(update_fields=("deleted_at",))

    @property
    def is_deleted(self):
        """Check if the user is soft-deleted."""
        return self.deleted_at is not None
