"""Django admin configuration for projects app."""

from django.contrib import admin
from django.utils.safestring import mark_safe
import json
from unfold.admin import ModelAdmin

from .models import Brief
from .models import Offer
from .models import OfferEntry
from .models import OfferRate
from .models import Project
from .models import Rate
from .models import Share
from .models import SimpleRate


@admin.register(Brief)
class BriefAdmin(ModelAdmin):
    """Brief admin configuration."""

    list_display = ["id", "status", "client", "created_at"]
    search_fields = ["id", "client__name"]
    list_filter = ["status", "created_at"]
    readonly_fields = ["created_at", "updated_at", "deleted_at"]
    ordering = ["-created_at"]


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    """Project admin configuration."""

    list_display = ["name", "vendor", "brief", "team", "status", "created_at"]
    search_fields = ["name", "vendor__name", "team__name"]
    list_filter = ["status", "created_at"]
    readonly_fields = ["created_at", "updated_at", "deleted_at"]
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


@admin.register(Offer)
class OfferAdmin(ModelAdmin):
    """Offer admin configuration."""

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
        "pretty_details",
    ]
    ordering = ["-created_at"]

    @admin.display(description="Details")
    def pretty_details(self, instance):
        return mark_safe(
            f'<pre style="background: #f1f1f1; padding: 10px; border: 1px solid #ddd; border-radius: 4px;">'
            f'{json.dumps(instance.details, indent=4, ensure_ascii=False)}'
            f'</pre>'
        )


@admin.register(OfferEntry)
class OfferEntryAdmin(ModelAdmin):
    """OfferEntry admin configuration."""

    list_display = ["offer", "entry", "base_price", "total_price", "created_at"]
    search_fields = ["offer__project_name", "entry__name"]
    list_filter = ["created_at"]
    readonly_fields = ["created_at", "updated_at", "deleted_at"]
    ordering = ["-created_at"]


@admin.register(OfferRate)
class OfferRateAdmin(ModelAdmin):
    """OfferRate admin configuration."""

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


@admin.register(Share)
class ShareAdmin(ModelAdmin):
    """Share admin configuration."""

    list_display = ["offer", "type", "status", "created_at"]
    search_fields = ["offer__project_name", "link"]
    list_filter = ["type", "status", "created_at"]
    readonly_fields = ["created_at", "updated_at", "deleted_at"]
    ordering = ["-created_at"]
