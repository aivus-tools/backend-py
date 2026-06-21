"""Projects models: Brief, Offer, Rate, Share, BriefOffer and related models."""

import secrets
import uuid
from decimal import Decimal

from django.db import models

from aivus_backend.catalog.models import Category
from aivus_backend.catalog.models import Entry
from aivus_backend.core.enums import BriefPromptSlug
from aivus_backend.core.enums import BriefSource
from aivus_backend.core.enums import BriefStatus
from aivus_backend.core.enums import ConversationStatus
from aivus_backend.core.enums import FeedbackRating
from aivus_backend.core.enums import FinalDocumentKind
from aivus_backend.core.enums import OfferSource
from aivus_backend.core.enums import OfferStatus
from aivus_backend.core.enums import ProjectStatus
from aivus_backend.core.enums import ShareStatus
from aivus_backend.core.enums import ShareType
from aivus_backend.core.managers import JournalizeManager
from aivus_backend.users.models import Client
from aivus_backend.users.models import Team
from aivus_backend.users.models import User
from aivus_backend.users.models import Vendor


# Legacy: referenced by old migration 0018. Kept as a stub so historical
# migrations can still be imported. The field it was attached to is dropped
# in migration 0025.
def _default_sections_status():
    return {}


class Brief(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(
        max_length=20, choices=BriefStatus.choices, default=BriefStatus.DRAFT
    )
    source = models.CharField(
        max_length=20,
        choices=BriefSource.choices,
        default=BriefSource.DIRECT,
        db_index=True,
    )
    details = models.JSONField(default=dict)
    structured_data = models.JSONField(default=dict, blank=True)
    client = models.ForeignKey(
        Client,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="briefs",
    )

    title = models.CharField(max_length=255, blank=True, default="")
    contact_email = models.CharField(max_length=254, blank=True, default="")
    contact_name = models.CharField(max_length=255, blank=True, default="")
    pending_task_id = models.CharField(max_length=64, blank=True, default="")
    pending_task_started_at = models.DateTimeField(null=True, blank=True)
    pending_task_error = models.CharField(max_length=64, blank=True, default="")
    finalize_failed = models.BooleanField(default=False)
    document_language = models.CharField(max_length=10, blank=True, default="")
    conversation_status = models.CharField(
        max_length=20,
        choices=ConversationStatus.choices,
        default=ConversationStatus.IN_PROGRESS,
    )
    anonymous_token = models.CharField(
        max_length=64, unique=True, null=True, blank=True, db_index=True
    )
    claimed_at = models.DateTimeField(null=True, blank=True)
    total_input_tokens = models.IntegerField(default=0)
    total_output_tokens = models.IntegerField(default=0)
    total_cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    message_count = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "brief"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Brief {self.id} - {self.status}"


class Project(models.Model):
    """Project model - vendor's work on a brief."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    vendor = models.ForeignKey(
        Vendor,
        on_delete=models.PROTECT,
        related_name="projects",
    )
    brief = models.ForeignKey(
        Brief,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="projects",
    )
    team = models.ForeignKey(
        Team,
        on_delete=models.PROTECT,
        related_name="projects",
        null=True,
        blank=True,
    )
    # QA4-052: Add default status
    status = models.CharField(
        max_length=20, choices=ProjectStatus.choices, default=ProjectStatus.DRAFT
    )

    # New fields for project details (moved from Brief.details JSON)
    crm_id = models.CharField(max_length=255, blank=True, default="")
    description = models.TextField(blank=True, default="")
    client = models.ForeignKey(
        Client,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="projects",
    )
    client_name = models.CharField(max_length=255, blank=True, default="")
    irs_ein = models.CharField(max_length=50, blank=True, default="")
    brand_name = models.CharField(max_length=255, blank=True, default="")
    agency_name = models.CharField(max_length=255, blank=True, default="")
    thumbnail = models.ImageField(
        upload_to="project_thumbnails/",
        null=True,
        blank=True,
    )

    emails_sent_at = models.DateTimeField(null=True, blank=True)
    vendor_notified_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = JournalizeManager()

    class Meta:
        db_table = "project"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["vendor", "brief"],
                condition=models.Q(deleted_at__isnull=True, brief__isnull=False),
                name="uniq_active_project_per_vendor_brief",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.vendor.name})"

    def restore(self):
        self.deleted_at = None
        self.save(update_fields=("deleted_at",))


class ProjectCollaborator(models.Model):
    """Project collaborators - can be internal users or external people."""

    ROLE_CHOICES = [
        ("internal_user", "Internal User"),
        ("external_user", "External User"),
        ("producer", "Producer"),
        ("agency_producer", "Agency Producer"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="collaborators",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="project_collaborations",
    )
    name = models.CharField(max_length=255)
    email = models.EmailField(blank=True, default="")
    role = models.CharField(
        max_length=20, choices=ROLE_CHOICES, default="internal_user"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "project_collaborator"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} - {self.project.name}"


class ClientManager(models.Model):
    """Client's managers for a project."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="client_managers",
    )
    name = models.CharField(max_length=255)
    position = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "client_manager"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.position}) - {self.project.name}"


class SimpleRate(models.Model):
    """Simple rate without options."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    vendor = models.ForeignKey(
        Vendor,
        on_delete=models.CASCADE,
        related_name="simple_rates",
    )
    entry = models.ForeignKey(
        Entry,
        on_delete=models.CASCADE,
        related_name="simple_rates",
    )
    # QA3-038: Changed from IntegerField to DecimalField to support decimal rates
    value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "rate"
        unique_together = [["vendor", "entry"]]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.vendor.name} - {self.entry.name}: ${self.value}"


class Rate(models.Model):
    """Rate with options (Rate Card)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="rates")
    entry = models.ForeignKey(
        Entry,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rates",
    )  # Nullable - can be custom or forked from entry
    base_price = models.DecimalField(max_digits=10, decimal_places=2)
    total_price = models.DecimalField(max_digits=10, decimal_places=2)
    options = models.JSONField(default=list)  # Array of RateOption objects
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "rate_card"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} - ${self.total_price}"

    def calculate_total_price(self):
        """Calculate total price from base price and options."""
        total = Decimal(str(self.base_price))

        for option in self.options:
            value = Decimal(str(option["value"]))
            if option["type"] == "fixed":
                total += value
            elif option["type"] == "percentage":
                total += total * value / 100

        return total.quantize(Decimal("0.01"))

    def save(self, *args, **kwargs):  # noqa: DJ012
        """Auto-calculate total_price before saving."""
        self.total_price = self.calculate_total_price()
        super().save(*args, **kwargs)

    @property
    def is_custom(self):
        """Check if rate is custom (not forked from entry)."""
        return self.entry_id is None


class Offer(models.Model):
    """Offer/Proposal model."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project_name = models.CharField(max_length=255)
    parent_offer = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="child_offers",
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="offers",
        null=True,
        blank=True,
    )
    description = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=20,
        choices=OfferStatus.choices,
        default=OfferStatus.DRAFT,
    )
    cost = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, null=True, blank=True
    )
    profit = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, null=True, blank=True
    )
    details = models.JSONField(default=dict)
    metadata = models.JSONField(default=dict, blank=True)
    bid_date = models.DateField(null=True, blank=True)
    revision = models.CharField(max_length=50, blank=True, default="")
    term = models.CharField(max_length=50, blank=True, default="6 months")
    territory = models.JSONField(default=list, blank=True)
    media_placements = models.JSONField(default=list, blank=True)
    cover_page_notes = models.TextField(blank=True, default="")
    assumptions_exclusions = models.TextField(blank=True, default="")
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
    deadline = models.DateTimeField(null=True, blank=True)
    source = models.CharField(max_length=20, choices=OfferSource.choices)
    is_locked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "offer"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.project_name} ({self.status})"


class OfferEntry(models.Model):
    """Parsed line item from Offer.details JSON."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    offer = models.ForeignKey(
        Offer,
        on_delete=models.CASCADE,
        related_name="offer_entries",
    )
    frontend_id = models.CharField(max_length=255, blank=True, default="")
    item_name = models.CharField(max_length=500, blank=True, default="")
    entry = models.ForeignKey(
        Entry,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="offer_entries",
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="offer_entries",
    )
    price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    client_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    client_cost = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    surcharge = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    tax_rate = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    tax_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    show_tax = models.BooleanField(default=False)
    overtime = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_linked_surcharge = models.BooleanField(default=True)
    market_range = models.CharField(max_length=50, blank=True, default="")
    item_data = models.JSONField(default=dict, blank=True)
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "offer_entry"
        ordering = ["sort_order", "-created_at"]

    def __str__(self):
        return f"{self.offer.project_name} - {self.item_name or self.frontend_id}"


class OfferRate(models.Model):
    """Many-to-many relationship between Offer and Rate with snapshot."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    offer = models.ForeignKey(
        Offer,
        on_delete=models.CASCADE,
        related_name="offer_rates",
    )
    rate = models.ForeignKey(
        Rate,
        on_delete=models.CASCADE,
        related_name="offer_rates",
    )

    # Snapshot data from rate at the moment of addition
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    base_price = models.DecimalField(max_digits=10, decimal_places=2)
    total_price = models.DecimalField(max_digits=10, decimal_places=2)
    options = models.JSONField(default=list)
    quantity = models.IntegerField(default=1)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "offer_rate"
        unique_together = [["offer", "rate"]]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.offer.project_name} - {self.name}"


class OfferDeliverable(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    offer = models.ForeignKey(
        Offer, on_delete=models.CASCADE, related_name="deliverables"
    )
    quantity = models.PositiveIntegerField(default=1)
    duration = models.CharField(max_length=20, blank=True, default="")
    duration_unit = models.CharField(max_length=10, blank=True, default="Sec")
    notes = models.TextField(blank=True, default="")
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "offer_deliverable"
        ordering = ["sort_order", "-created_at"]

    def __str__(self):
        return (
            f"{self.offer.project_name}"
            f" - {self.quantity}x"
            f" {self.duration}{self.duration_unit}"
        )


class OfferScheduleEntry(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    offer = models.ForeignKey(
        Offer, on_delete=models.CASCADE, related_name="schedule_entries"
    )
    phase_type = models.CharField(max_length=100, default="Prep")
    days = models.PositiveIntegerField(default=1)
    hours_per_day = models.PositiveIntegerField(default=12)
    notes = models.TextField(blank=True, default="")
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "offer_schedule_entry"
        ordering = ["sort_order", "-created_at"]

    def __str__(self):
        return (
            f"{self.offer.project_name}"
            f" - {self.phase_type}"
            f" ({self.days}d @ {self.hours_per_day}h)"
        )


class Share(models.Model):
    """Share link for offers — allows public access via unique token."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    offer = models.ForeignKey(Offer, on_delete=models.CASCADE, related_name="shares")
    token = models.CharField(
        max_length=64, unique=True, db_index=True, default=secrets.token_urlsafe
    )
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_shares",
    )
    # Keep legacy fields for backward compatibility with existing data
    type = models.CharField(
        max_length=10, choices=ShareType.choices, blank=True, default=""
    )
    link = models.CharField(max_length=500, blank=True, default="")
    status = models.CharField(
        max_length=20, choices=ShareStatus.choices, blank=True, default=""
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "share"
        ordering = ["-created_at"]

    def __str__(self):
        status = "active" if self.is_active else "inactive"
        return f"{self.offer.project_name} - token:{self.token[:8]}... ({status})"


class BriefOffer(models.Model):
    """Links a shared offer to a client's brief."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    brief = models.ForeignKey(
        Brief,
        on_delete=models.CASCADE,
        related_name="brief_offers",
    )
    offer = models.ForeignKey(
        Offer,
        on_delete=models.CASCADE,
        related_name="brief_offers",
    )
    linked_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="linked_brief_offers",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "brief_offer"
        unique_together = [["brief", "offer"]]
        ordering = ["-created_at"]

    def __str__(self):
        return f"Brief {self.brief_id} <-> Offer {self.offer.project_name}"


class Template(models.Model):
    """Template model — full snapshot of an offer for reuse."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    vendor = models.ForeignKey(
        Vendor,
        on_delete=models.CASCADE,
        related_name="templates",
    )
    source_offer = models.ForeignKey(
        Offer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="templates",
    )
    details = models.JSONField(default=dict)  # Full snapshot of offer details
    description = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)  # e.g. categories, totals
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "template"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.vendor.name})"


class RateCard(models.Model):
    """Rate card — a named collection of standard prices for a vendor."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    vendor = models.ForeignKey(
        Vendor,
        on_delete=models.CASCADE,
        related_name="rate_cards",
    )
    name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "rate_card_v2"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.vendor.name})"


class ChatMessage(models.Model):
    ROLE_CHOICES = [
        ("user", "User"),
        ("assistant", "Assistant"),
    ]
    KIND_CHAT = "chat"
    KIND_FEEDBACK_REQUEST = "feedback_request"
    KIND_FEEDBACK_REPLY_ACK = "feedback_reply_ack"
    KIND_CHOICES = [
        (KIND_CHAT, "Chat"),
        (KIND_FEEDBACK_REQUEST, "Feedback Request"),
        (KIND_FEEDBACK_REPLY_ACK, "Feedback Reply Ack"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    brief = models.ForeignKey(
        Brief,
        on_delete=models.CASCADE,
        related_name="chat_messages",
        null=True,
        blank=True,
    )
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="chat_messages",
    )
    anonymous_token = models.CharField(max_length=64, blank=True, default="")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    kind = models.CharField(
        max_length=32, choices=KIND_CHOICES, default=KIND_CHAT, db_index=True
    )
    content = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)
    input_tokens = models.IntegerField(default=0)
    output_tokens = models.IntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    model_used = models.CharField(max_length=100, blank=True, default="")
    ready_to_finalize = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "chat_message"
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.role}: {self.content[:50]}..."


class RateCardItem(models.Model):
    """Individual rate item within a rate card."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rate_card = models.ForeignKey(
        RateCard,
        on_delete=models.CASCADE,
        related_name="items",
    )
    entry = models.ForeignKey(
        Entry,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rate_card_items",
    )
    item_name = models.CharField(max_length=255)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    unit = models.ForeignKey(
        "catalog.Unit",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rate_card_items",
    )
    unit_label = models.CharField(max_length=50, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "rate_card_item"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.item_name} - ${self.price}"


class BriefFeedback(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    brief = models.ForeignKey(
        Brief,
        on_delete=models.CASCADE,
        related_name="feedbacks",
    )
    message = models.ForeignKey(
        ChatMessage,
        on_delete=models.CASCADE,
        related_name="feedbacks",
    )
    rating = models.CharField(max_length=10, choices=FeedbackRating.choices)
    comment = models.TextField(blank=True, default="")
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="brief_feedbacks",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "brief_feedback"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.rating} on {self.brief_id} by {self.user_id}"


class LLMCallTrace(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    message = models.ForeignKey(
        ChatMessage,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="llm_traces",
    )
    final_document = models.ForeignKey(
        "BriefFinalDocument",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="llm_traces",
    )
    purpose = models.CharField(max_length=32, blank=True, default="")
    model = models.CharField(max_length=100, blank=True, default="")
    request_messages = models.JSONField(default=list, blank=True)
    request_params = models.JSONField(default=dict, blank=True)
    response_raw = models.TextField(blank=True, default="")
    input_tokens = models.IntegerField(default=0)
    output_tokens = models.IntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    latency_ms = models.IntegerField(default=0)
    sequence = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "llm_call_trace"
        ordering = ["message_id", "sequence", "created_at"]

    def __str__(self):
        return f"{self.purpose} ({self.model}) for message {self.message_id}"


def _brief_attachment_upload_to(instance: "BriefAttachment", filename: str) -> str:
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    return f"briefs/{instance.brief_id}/{uuid.uuid4()}.{suffix}"


class BriefShare(models.Model):
    """Public share link for a finalized brief.

    Simple model — one BriefShare per brief. Token is a urlsafe 64-char string
    generated on creation. Active/inactive toggle lets the owner revoke the
    link without deleting it.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    brief = models.OneToOneField(
        "Brief",
        on_delete=models.CASCADE,
        related_name="share",
    )
    token = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        default=secrets.token_urlsafe,
    )
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_brief_shares",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "brief_share"
        ordering = ["-created_at"]

    def __str__(self):
        state = "active" if self.is_active else "inactive"
        return f"Share for {self.brief_id} ({state})"


class BriefPrompt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.CharField(
        max_length=64,
        choices=BriefPromptSlug.choices,
        db_index=True,
    )
    title = models.CharField(max_length=255)
    body = models.TextField()
    version = models.IntegerField(default=1)
    is_active = models.BooleanField(default=False, db_index=True)
    model_name = models.CharField(max_length=100, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_brief_prompts",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "brief_prompt"
        ordering = ["slug", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["slug", "version"],
                name="brief_prompt_slug_version_unique",
            ),
            models.UniqueConstraint(
                fields=["slug"],
                condition=models.Q(is_active=True),
                name="brief_prompt_active_single",
            ),
        ]

    def __str__(self):
        state = "active" if self.is_active else "inactive"
        return f"{self.slug} v{self.version} ({state})"

    @classmethod
    def get_active_body(cls, slug: str, default: str = "") -> str:
        row = cls.objects.filter(slug=slug, is_active=True).only("body").first()
        return row.body if row else default

    @classmethod
    def get_active(cls, slug: str) -> "BriefPrompt | None":
        return cls.objects.filter(slug=slug, is_active=True).first()


class BriefAttachment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    brief = models.ForeignKey(
        Brief,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    message = models.ForeignKey(
        ChatMessage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attachments",
    )
    file = models.FileField(upload_to=_brief_attachment_upload_to)
    filename = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=128)
    size_bytes = models.BigIntegerField(default=0)
    gemini_file_uri = models.CharField(max_length=512, blank=True, default="")
    extracted_text = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "brief_attachment"
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.filename} ({self.mime_type})"


class BriefFinalDocument(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    brief = models.ForeignKey(
        Brief,
        on_delete=models.CASCADE,
        related_name="final_documents",
    )
    kind = models.CharField(max_length=32, choices=FinalDocumentKind.choices)
    html = models.TextField(blank=True, default="")
    plain_text = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "brief_final_document"
        ordering = ["brief_id", "kind"]
        constraints = [
            models.UniqueConstraint(
                fields=["brief", "kind"],
                name="brief_final_document_unique_kind",
            ),
        ]

    def __str__(self):
        return f"{self.kind} for brief {self.brief_id}"
