import uuid
from typing import ClassVar

from django.contrib.auth.hashers import check_password as django_check_password
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from aivus_backend.core.enums import AuthType
from aivus_backend.core.enums import TeamRole
from aivus_backend.core.enums import UserGroup

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

    # Additional fields for Aivus
    group = models.CharField(
        max_length=20,
        choices=UserGroup.choices,
        default=UserGroup.UNCONFIRMED,
        db_index=True,
    )
    position = models.CharField(max_length=255, blank=True, default="")
    avatar = models.ImageField(upload_to="avatars/", null=True, blank=True)
    auth_type = models.CharField(
        max_length=20,
        choices=AuthType.choices,
        default=AuthType.CREDENTIALS,
    )

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects: ClassVar[UserManager] = UserManager()

    class Meta:
        db_table = "user"
        ordering = ["-created_at"]

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

    def check_plain_password(self, raw_password: str) -> bool:
        """Check password without Django auth system."""
        if not self.password:
            return False
        return django_check_password(raw_password, self.password)


class Client(models.Model):
    """Client company model."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ein = models.CharField(max_length=255)  # Employer Identification Number
    name = models.CharField(max_length=255)
    owner = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="clients",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "client"
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class Vendor(models.Model):
    """Vendor/Agency company model."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    owner = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="agencies",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "vendor"
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class Team(models.Model):
    """Team model for brief collaboration."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "team"
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class UserTeam(models.Model):
    """Many-to-many relationship between User and Team with role."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="user_teams",
    )
    team = models.ForeignKey(
        Team,
        on_delete=models.CASCADE,
        related_name="user_teams",
    )
    role = models.CharField(max_length=20, choices=TeamRole.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "user_team"
        unique_together = [["user", "team"]]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.name} - {self.team.name} ({self.role})"


class UserSettings(models.Model):
    """User settings for preferences and notifications."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="settings",
    )
    language = models.CharField(max_length=5, default="en")
    nda_accepted = models.BooleanField(default=False)
    notification_email = models.BooleanField(default=True)
    notification_browser = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "user_settings"

    def __str__(self):
        return f"Settings for {self.user.email}"


class VendorSettings(models.Model):
    """Vendor-level settings: branding, default percentages for offers."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    vendor = models.OneToOneField(
        Vendor,
        on_delete=models.CASCADE,
        related_name="vendor_settings",
    )
    logo = models.ImageField(upload_to="vendor_logos/", null=True, blank=True)
    company_name = models.CharField(max_length=255, blank=True, default="")
    agency_name = models.CharField(max_length=255, blank=True, default="")
    fringes_percent = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    handling_percent = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    markup_percent = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    production_insurance_percent = models.DecimalField(
        max_digits=6, decimal_places=2, default=0
    )
    production_fee_percent = models.DecimalField(
        max_digits=6, decimal_places=2, default=0
    )
    post_markup_percent = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    post_insurance_percent = models.DecimalField(
        max_digits=6, decimal_places=2, default=0
    )
    post_tax_percent = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "vendor_settings"

    def __str__(self):
        return f"Settings for {self.vendor.name}"


# Import AuthToken to make it visible to Django migrations
from .tokens import AuthToken  # noqa: E402
from .tokens import TokenType  # noqa: E402

__all__ = [
    "AuthToken",
    "Client",
    "Team",
    "TokenType",
    "User",
    "UserSettings",
    "UserTeam",
    "Vendor",
    "VendorSettings",
]
