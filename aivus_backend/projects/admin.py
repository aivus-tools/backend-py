import json

from django import forms
from django.contrib import admin
from django.utils.safestring import mark_safe
from tinymce.widgets import TinyMCE
from unfold.admin import ModelAdmin

from .models import Brief
from .models import BriefAttachment
from .models import BriefFeedback
from .models import BriefFinalDocument
from .models import BriefOffer
from .models import BriefPrompt
from .models import ChatMessage
from .models import ClientManager
from .models import Offer
from .models import OfferDeliverable
from .models import OfferEntry
from .models import OfferRate
from .models import OfferScheduleEntry
from .models import Project
from .models import ProjectCollaborator
from .models import Rate
from .models import RateCard
from .models import RateCardItem
from .models import Share
from .models import SimpleRate
from .models import Template

CONTENT_PREVIEW_LENGTH = 80


@admin.register(Brief)
class BriefAdmin(ModelAdmin):
    list_display = [
        "id",
        "status",
        "conversation_status",
        "title",
        "document_language",
        "client",
        "total_cost_usd",
        "message_count",
        "created_at",
    ]
    search_fields = ["id", "title", "client__name", "anonymous_token"]
    list_filter = ["status", "conversation_status", "document_language", "created_at"]
    readonly_fields = [
        "created_at",
        "updated_at",
        "deleted_at",
        "total_input_tokens",
        "total_output_tokens",
        "total_cost_usd",
        "message_count",
    ]
    ordering = ["-created_at"]


class ProjectCollaboratorInline(admin.TabularInline):
    """Inline for ProjectCollaborator in Project admin."""

    model = ProjectCollaborator
    extra = 0
    readonly_fields = ["created_at"]
    fields = ["user", "name", "email", "role", "created_at"]


class ClientManagerInline(admin.TabularInline):
    """Inline for ClientManager in Project admin."""

    model = ClientManager
    extra = 0
    readonly_fields = ["created_at"]
    fields = ["name", "position", "created_at"]


@admin.register(Project)
class ProjectAdmin(ModelAdmin):
    """Project admin configuration."""

    list_display = [
        "name",
        "vendor",
        "client",
        "brand_name",
        "status",
        "created_at",
    ]
    search_fields = ["name", "vendor__name", "client__name", "brand_name", "crm_id"]
    list_filter = ["status", "vendor", "created_at"]
    readonly_fields = ["created_at", "updated_at", "deleted_at"]
    ordering = ["-created_at"]

    fieldsets = (
        (
            "Basic Info",
            {
                "fields": ("name", "vendor", "status", "crm_id", "description"),
            },
        ),
        (
            "Client Info",
            {
                "fields": ("client", "irs_ein", "brand_name"),
            },
        ),
        (
            "Media",
            {
                "fields": ("thumbnail",),
            },
        ),
        (
            "Relations",
            {
                "fields": ("brief", "team"),
            },
        ),
        (
            "Timestamps",
            {
                "fields": ("created_at", "updated_at", "deleted_at"),
                "classes": ("collapse",),
            },
        ),
    )

    inlines = [ProjectCollaboratorInline, ClientManagerInline]


@admin.register(ProjectCollaborator)
class ProjectCollaboratorAdmin(ModelAdmin):
    """ProjectCollaborator admin configuration."""

    list_display = ["project", "user", "name", "email", "role", "created_at"]
    search_fields = ["project__name", "user__name", "name", "email"]
    list_filter = ["role", "created_at"]
    readonly_fields = ["created_at", "updated_at"]
    ordering = ["-created_at"]


@admin.register(ClientManager)
class ClientManagerAdmin(ModelAdmin):
    """ClientManager admin configuration."""

    list_display = ["project", "name", "position", "created_at"]
    search_fields = ["project__name", "name"]
    list_filter = ["created_at"]
    readonly_fields = ["created_at", "updated_at"]
    ordering = ["-created_at"]


@admin.register(SimpleRate)
class SimpleRateAdmin(ModelAdmin):
    """SimpleRate admin configuration."""

    list_display = ["vendor", "entry", "value", "created_at"]
    search_fields = ["vendor__name", "entry__name"]
    list_filter = ["created_at"]
    readonly_fields = ["created_at", "updated_at", "deleted_at"]
    ordering = ["-created_at"]


@admin.register(Rate)
class RateAdmin(ModelAdmin):
    """Rate admin configuration."""

    list_display = [
        "name",
        "vendor",
        "entry",
        "base_price",
        "total_price",
        "created_at",
    ]
    search_fields = ["name", "vendor__name", "entry__name"]
    list_filter = ["vendor", "created_at"]
    readonly_fields = ["created_at", "updated_at", "deleted_at"]
    ordering = ["-created_at"]


class OfferAdminForm(forms.ModelForm):
    cover_page_notes = forms.CharField(
        widget=TinyMCE(attrs={"cols": 80, "rows": 20}),
        required=False,
    )

    class Meta:
        model = Offer
        fields = "__all__"  # noqa: DJ007


class OfferDeliverableInline(admin.TabularInline):
    model = OfferDeliverable
    extra = 0
    readonly_fields = ["created_at"]
    fields = [
        "quantity",
        "duration",
        "duration_unit",
        "notes",
        "sort_order",
        "created_at",
    ]


class OfferScheduleEntryInline(admin.TabularInline):
    model = OfferScheduleEntry
    extra = 0
    readonly_fields = ["created_at"]
    fields = [
        "phase_type",
        "days",
        "hours_per_day",
        "notes",
        "sort_order",
        "created_at",
    ]


@admin.register(Offer)
class OfferAdmin(ModelAdmin):
    form = OfferAdminForm

    list_display = [
        "project_name",
        "project",
        "status",
        "deadline",
        "is_locked",
        "created_at",
    ]
    search_fields = ["project_name", "project__name", "project__vendor__name"]
    list_filter = ["status", "source", "is_locked", "created_at"]
    readonly_fields = ["pretty_details", "created_at", "updated_at", "deleted_at"]
    fields = [
        "project_name",
        "project",
        "status",
        "deadline",
        "source",
        "is_locked",
        "bid_date",
        "revision",
        "term",
        "territory",
        "media_placements",
        "cover_page_notes",
        "pretty_details",
        "created_at",
        "updated_at",
        "deleted_at",
    ]
    ordering = ["-created_at"]
    inlines = [OfferDeliverableInline, OfferScheduleEntryInline]

    @admin.display(description="Details")
    def pretty_details(self, instance):
        pre_style = (
            "background: #f1f1f1; padding: 10px;"
            " border: 1px solid #ddd; border-radius: 4px;"
        )
        content = json.dumps(instance.details, indent=4, ensure_ascii=True)
        return mark_safe(  # noqa: S308
            f'<pre style="{pre_style}">{content}</pre>'
        )


@admin.register(OfferEntry)
class OfferEntryAdmin(ModelAdmin):
    """OfferEntry admin configuration."""

    list_display = [
        "offer",
        "item_name",
        "entry",
        "price",
        "cost",
        "sort_order",
        "created_at",
    ]
    search_fields = ["offer__project_name", "item_name", "frontend_id"]
    list_filter = ["show_tax", "is_linked_surcharge", "created_at"]
    readonly_fields = ["created_at", "updated_at", "deleted_at"]
    ordering = ["sort_order", "-created_at"]


@admin.register(OfferRate)
class OfferRateAdmin(ModelAdmin):
    list_display = [
        "offer",
        "name",
        "base_price",
        "total_price",
        "quantity",
        "created_at",
    ]
    search_fields = ["offer__project_name", "name"]
    list_filter = ["created_at"]
    readonly_fields = ["created_at", "updated_at", "deleted_at"]
    ordering = ["-created_at"]


@admin.register(OfferDeliverable)
class OfferDeliverableAdmin(ModelAdmin):
    list_display = [
        "offer",
        "quantity",
        "duration",
        "duration_unit",
        "sort_order",
        "created_at",
    ]
    search_fields = ["offer__project_name"]
    list_filter = ["created_at"]
    readonly_fields = ["created_at", "updated_at", "deleted_at"]
    ordering = ["sort_order", "-created_at"]


@admin.register(OfferScheduleEntry)
class OfferScheduleEntryAdmin(ModelAdmin):
    list_display = [
        "offer",
        "phase_type",
        "days",
        "hours_per_day",
        "sort_order",
        "created_at",
    ]
    search_fields = ["offer__project_name", "phase_type"]
    list_filter = ["created_at"]
    readonly_fields = ["created_at", "updated_at", "deleted_at"]
    ordering = ["sort_order", "-created_at"]


@admin.register(Share)
class ShareAdmin(ModelAdmin):
    """Share admin configuration."""

    list_display = ["offer", "token_short", "is_active", "created_by", "created_at"]
    search_fields = ["offer__project_name", "token"]
    list_filter = ["is_active", "created_at"]
    readonly_fields = ["token", "created_at", "updated_at", "deleted_at"]
    ordering = ["-created_at"]

    @admin.display(description="Token")
    def token_short(self, instance):
        return f"{instance.token[:12]}..." if instance.token else ""


@admin.register(BriefOffer)
class BriefOfferAdmin(ModelAdmin):
    """BriefOffer admin configuration."""

    list_display = ["brief", "offer", "linked_by", "created_at"]
    search_fields = ["brief__id", "offer__project_name"]
    list_filter = ["created_at"]
    readonly_fields = ["created_at", "updated_at"]
    ordering = ["-created_at"]


@admin.register(Template)
class TemplateAdmin(ModelAdmin):
    """Template admin configuration."""

    list_display = ["name", "vendor", "source_offer", "created_at"]
    search_fields = ["name", "vendor__name"]
    list_filter = ["vendor", "created_at"]
    readonly_fields = ["pretty_details", "created_at", "updated_at", "deleted_at"]
    fields = [
        "name",
        "vendor",
        "source_offer",
        "description",
        "pretty_details",
        "created_at",
        "updated_at",
        "deleted_at",
    ]
    ordering = ["-created_at"]

    @admin.display(description="Details")
    def pretty_details(self, instance):
        pre_style = (
            "background: #f1f1f1; padding: 10px;"
            " border: 1px solid #ddd; border-radius: 4px;"
        )
        content = json.dumps(instance.details, indent=4, ensure_ascii=True)
        return mark_safe(  # noqa: S308
            f'<pre style="{pre_style}">{content}</pre>'
        )


class RateCardItemInline(admin.TabularInline):
    """Inline for RateCardItem in RateCard admin."""

    model = RateCardItem
    extra = 0
    readonly_fields = ["created_at"]
    fields = ["item_name", "entry", "price", "unit", "unit_label", "created_at"]


@admin.register(RateCard)
class RateCardAdmin(ModelAdmin):
    """RateCard admin configuration."""

    list_display = ["name", "vendor", "items_count", "created_at"]
    search_fields = ["name", "vendor__name"]
    list_filter = ["vendor", "created_at"]
    readonly_fields = ["created_at", "updated_at", "deleted_at"]
    ordering = ["-created_at"]
    inlines = [RateCardItemInline]

    @admin.display(description="Items")
    def items_count(self, instance):
        return instance.items.filter(deleted_at__isnull=True).count()


@admin.register(RateCardItem)
class RateCardItemAdmin(ModelAdmin):
    """RateCardItem admin configuration."""

    list_display = ["rate_card", "item_name", "entry", "price", "unit", "created_at"]
    search_fields = ["rate_card__name", "item_name", "entry__name"]
    list_filter = ["created_at"]
    readonly_fields = ["created_at", "updated_at", "deleted_at"]
    ordering = ["-created_at"]


@admin.register(ChatMessage)
class ChatMessageAdmin(ModelAdmin):
    list_display = [
        "brief",
        "user",
        "role",
        "model_used",
        "cost_usd",
        "ready_to_finalize",
        "content_short",
        "created_at",
    ]
    search_fields = ["content", "user__email", "brief__id"]
    list_filter = ["role", "model_used", "ready_to_finalize", "created_at"]
    readonly_fields = [
        "created_at",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "model_used",
    ]
    ordering = ["-created_at"]

    @admin.display(description="Content")
    def content_short(self, instance):
        return (
            instance.content[:CONTENT_PREVIEW_LENGTH] + "..."
            if len(instance.content) > CONTENT_PREVIEW_LENGTH
            else instance.content
        )


class BriefPromptAdminForm(forms.ModelForm):
    body = forms.CharField(
        widget=TinyMCE(attrs={"cols": 100, "rows": 30}),
        required=True,
    )

    class Meta:
        model = BriefPrompt
        fields = "__all__"  # noqa: DJ007


@admin.register(BriefPrompt)
class BriefPromptAdmin(ModelAdmin):
    form = BriefPromptAdminForm

    list_display = [
        "slug",
        "title",
        "version",
        "is_active",
        "model_name",
        "updated_at",
    ]
    list_filter = ["slug", "is_active"]
    search_fields = ["slug", "title", "body"]
    readonly_fields = ["version", "created_at", "updated_at", "created_by"]
    ordering = ["slug", "-version"]

    fieldsets = (
        (
            "Identity",
            {"fields": ("slug", "title", "version", "is_active", "model_name")},
        ),
        ("Content", {"fields": ("body",)}),
        ("Metadata", {"fields": ("metadata",), "classes": ("collapse",)}),
        (
            "Audit",
            {
                "fields": ("created_by", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    def save_model(self, request, obj, form, change):
        if not obj.pk:
            last = (
                BriefPrompt.objects.filter(slug=obj.slug)
                .order_by("-version")
                .only("version")
                .first()
            )
            obj.version = (last.version + 1) if last else 1
            if getattr(request, "user", None) and request.user.is_authenticated:
                obj.created_by = request.user

        if obj.is_active:
            BriefPrompt.objects.filter(slug=obj.slug, is_active=True).exclude(
                pk=obj.pk
            ).update(is_active=False)

        super().save_model(request, obj, form, change)


@admin.register(BriefAttachment)
class BriefAttachmentAdmin(ModelAdmin):
    list_display = [
        "filename",
        "brief",
        "mime_type",
        "size_bytes",
        "created_at",
    ]
    search_fields = ["filename", "brief__id"]
    list_filter = ["mime_type", "created_at"]
    readonly_fields = [
        "brief",
        "message",
        "file",
        "filename",
        "mime_type",
        "size_bytes",
        "gemini_file_uri",
        "created_at",
    ]
    ordering = ["-created_at"]

    def has_add_permission(self, request):
        return False


@admin.register(BriefFinalDocument)
class BriefFinalDocumentAdmin(ModelAdmin):
    list_display = ["brief", "kind", "updated_at"]
    search_fields = ["brief__id"]
    list_filter = ["kind", "created_at"]
    readonly_fields = [
        "brief",
        "kind",
        "html",
        "plain_text",
        "created_at",
        "updated_at",
    ]
    ordering = ["-updated_at"]

    def has_add_permission(self, request):
        return False


@admin.register(BriefFeedback)
class BriefFeedbackAdmin(ModelAdmin):
    list_display = [
        "brief",
        "rating",
        "user",
        "comment_short",
        "created_at",
    ]
    search_fields = ["comment", "brief__id"]
    list_filter = ["rating", "created_at"]
    readonly_fields = ["created_at"]
    ordering = ["-created_at"]

    @admin.display(description="Comment")
    def comment_short(self, instance):
        return (
            instance.comment[:CONTENT_PREVIEW_LENGTH] + "..."
            if len(instance.comment) > CONTENT_PREVIEW_LENGTH
            else instance.comment
        )
