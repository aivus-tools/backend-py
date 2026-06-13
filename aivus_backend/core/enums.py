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


class BriefSource(models.TextChoices):
    """Where the brief originated from."""

    DIRECT = "direct", "Direct"
    PERSONAL_LINK = "personal_link", "Personal vendor link"
    WEBHOOK = "webhook", "Vendor webhook"
    WIX = "wix", "Wix landing form"


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


class ConversationStatus(models.TextChoices):
    IN_PROGRESS = "in_progress", "In Progress"
    READY_TO_FINALIZE = "ready_to_finalize", "Ready to Finalize"
    FINALIZED = "finalized", "Finalized"


class FeedbackRating(models.TextChoices):
    UP = "up", "Thumbs Up"
    DOWN = "down", "Thumbs Down"


class FinalDocumentKind(models.TextChoices):
    PRODUCTION_BRIEF = "production_brief", "Production Brief"
    VENDOR_EMAIL = "vendor_email", "Vendor Outreach Email"
    DELIVERABLES_CHECKLIST = "deliverables_checklist", "Deliverables Checklist"


class BriefPromptSlug(models.TextChoices):
    MAIN_SYSTEM = "main_system_prompt", "Main system prompt"
    FINALIZATION = "finalization_prompt", "Finalization prompt"
    MASTER_BRIEF_TEMPLATE = "master_brief_template", "Master brief template"
    ARCHETYPES_REFERENCE = "archetypes_reference", "Archetypes reference"
    STT_INDUSTRY_TERMS = "stt_industry_terms", "STT industry terms"


class UnitDimension(models.TextChoices):
    QUANTITY = "QUANTITY", "Quantity"
    TEMPORAL = "TEMPORAL", "Temporal"


class Language(models.TextChoices):
    """Supported content languages."""

    EN = "en", "English"
    RU = "ru", "Russian"
