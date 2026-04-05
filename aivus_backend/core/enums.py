"""Enums for the entire application."""

from django.db import models


class UserGroup(models.TextChoices):
    """User group choices."""

    UNCONFIRMED = "UNCONFIRMED", "Waiting to be validated"
    CONFIRMED = "CONFIRMED", "Did not choose the group"
    VENDOR = "VENDOR", "Vendor/Agency"
    CLIENT = "CLIENT", "Client"
    SYSTEM = "SYSTEM", "System"


class TeamRole(models.TextChoices):
    """Team role choices."""

    OWNER = "OWNER", "Owner"
    ADMIN = "ADMIN", "Admin"
    MEMBER = "MEMBER", "Member"
    EXTERNAL = "EXTERNAL", "External"


class ProjectStatus(models.TextChoices):
    """Project status choices."""

    DRAFT = "DRAFT", "Initial status"
    RFP = "RFP", "Request for Proposal"
    REVIEWING = "REVIEWING", "Under review"
    ONGOING = "ONGOING", "In progress"


class BriefStatus(models.TextChoices):
    """Brief status choices."""

    DRAFT = "DRAFT", "Draft"
    SUBMITTED = "SUBMITTED", "Submitted"
    REVIEWING = "REVIEWING", "Reviewing"
    COMPLETED = "COMPLETED", "Completed"


class AuthType(models.TextChoices):
    """Authentication type choices."""

    CREDENTIALS = "CREDENTIALS", "Email/Password"
    GOOGLE = "GOOGLE", "Google OAuth"


class OfferSource(models.TextChoices):
    """Offer source choices."""

    PLATFORM = "PLATFORM", "Default source"
    UPLOAD = "UPLOAD", "Uploaded"


class OfferStatus(models.TextChoices):
    """Offer status choices."""

    DRAFT = "DRAFT", "Draft"
    PUBLISHED = "PUBLISHED", "Published"
    ARCHIVED = "ARCHIVED", "Archived"


class ShareType(models.TextChoices):
    """Share type choices."""

    XLS = "XLS", "Excel"
    PDF = "PDF", "PDF"


class ShareStatus(models.TextChoices):
    """Share status choices."""

    PENDING = "PENDING", "Pending"
    READY = "READY", "Ready"
    DELETED = "DELETED", "Deleted"


class ConversationPhase(models.TextChoices):
    INITIAL = "initial", "Initial"
    QUESTIONING = "questioning", "Questioning"
    REFINING = "refining", "Refining"
    COMPLETE = "complete", "Complete"


class SectionStatus(models.TextChoices):
    EMPTY = "empty", "Empty"
    DRAFT = "draft", "Draft"
    COMPLETE = "complete", "Complete"


class FeedbackRating(models.TextChoices):
    UP = "up", "Thumbs Up"
    DOWN = "down", "Thumbs Down"


class UnitDimension(models.TextChoices):
    QUANTITY = "QUANTITY", "Quantity"
    TEMPORAL = "TEMPORAL", "Temporal"
