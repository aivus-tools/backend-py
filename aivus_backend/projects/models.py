"""Projects models: Brief, Offer, Rate, Share, BriefOffer and related models."""

import secrets
import uuid
from decimal import Decimal

from django.db import models

from aivus_backend.catalog.models import Category
from aivus_backend.catalog.models import Entry
from aivus_backend.core.enums import BriefStatus
from aivus_backend.core.enums import ConversationPhase
from aivus_backend.core.enums import FeedbackRating
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

BRIEF_SECTION_KEYS = [
    "project_header",
    "budget_timeline",
    "strategic_foundation",
    "creative_direction",
    "scope_video",
    "scope_photo",
    "post_production",
    "usage_rights",
    "deliverables",
]


def _default_sections_status():
    return dict.fromkeys(BRIEF_SECTION_KEYS, "empty")


class Brief(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(
        max_length=20, choices=BriefStatus.choices, default=BriefStatus.DRAFT
    )
    details = models.JSONField(default=dict)
    client = models.ForeignKey(
        Client,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="briefs",
    )

    document_sections = models.JSONField(default=dict, blank=True)
    structured_data = models.JSONField(default=dict, blank=True)
    archetypes = models.JSONField(default=list, blank=True)
    sections_status = models.JSONField(default=_default_sections_status, blank=True)
    conversation_phase = models.CharField(
        max_length=20,
        choices=ConversationPhase.choices,
        default=ConversationPhase.INITIAL,
    )
    version = models.IntegerField(default=0)
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

    def render_document_html(self) -> str:
        parts = []
        for key in BRIEF_SECTION_KEYS:
            html = self.document_sections.get(key, "")
            if html:
                parts.append(f'<div data-section="{key}">{html}</div>')
        return "\n<hr/>\n".join(parts)


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

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = JournalizeManager()

    class Meta:
        db_table = "project"
        ordering = ["-created_at"]

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
    content = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)
    input_tokens = models.IntegerField(default=0)
    output_tokens = models.IntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    model_used = models.CharField(max_length=100, blank=True, default="")
    sections_changed = models.JSONField(default=list, blank=True)
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


class BriefMethodology(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    archetype_code = models.IntegerField(null=True, blank=True, db_index=True)
    section_key = models.CharField(max_length=50, blank=True, default="", db_index=True)
    title = models.CharField(max_length=255)
    content = models.TextField()
    priority = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "brief_methodology"
        ordering = ["priority"]

    def __str__(self):
        archetype = f"A{self.archetype_code}" if self.archetype_code else "all"
        section = self.section_key or "general"
        return f"{self.title} ({archetype}/{section})"


class BriefFeedback(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    brief = models.ForeignKey(
        Brief,
        on_delete=models.CASCADE,
        related_name="feedbacks",
    )
    message = models.ForeignKey(
        ChatMessage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="feedbacks",
    )
    section_key = models.CharField(max_length=50, blank=True, default="")
    rating = models.CharField(max_length=10, choices=FeedbackRating.choices)
    comment = models.TextField(blank=True, default="")
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="brief_feedbacks",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "brief_feedback"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.rating} on {self.brief_id} by {self.user_id}"
