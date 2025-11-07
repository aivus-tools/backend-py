"""Django admin configuration for projects app."""

from django.contrib import admin

from .models import Brief
from .models import Offer
from .models import OfferEntry
from .models import OfferRate
from .models import Rate
from .models import Share
from .models import SimpleRate


@admin.register(Brief)
class BriefAdmin(admin.ModelAdmin):
    """Brief admin configuration."""

    list_display = ["id", "status", "client", "team", "created_at"]
    search_fields = ["id", "client__name", "team__name"]
    list_filter = ["status", "created_at"]
    readonly_fields = ["created_at", "updated_at", "deleted_at"]
    ordering = ["-created_at"]


@admin.register(SimpleRate)
class SimpleRateAdmin(admin.ModelAdmin):
    """SimpleRate admin configuration."""

    list_display = ["vendor", "entry", "value", "created_at"]
    search_fields = ["vendor__name", "entry__name"]
    list_filter = ["created_at"]
    readonly_fields = ["created_at", "updated_at", "deleted_at"]
    ordering = ["-created_at"]


@admin.register(Rate)
class RateAdmin(admin.ModelAdmin):
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
class OfferAdmin(admin.ModelAdmin):
    """Offer admin configuration."""

    list_display = [
        "project_name",
        "vendor",
        "status",
        "deadline",
        "is_locked",
        "created_at",
    ]
    search_fields = ["project_name", "vendor__name"]
    list_filter = ["status", "source", "is_locked", "created_at"]
    readonly_fields = ["created_at", "updated_at", "deleted_at"]
    ordering = ["-created_at"]


@admin.register(OfferEntry)
class OfferEntryAdmin(admin.ModelAdmin):
    """OfferEntry admin configuration."""

    list_display = ["offer", "entry", "base_price", "total_price", "created_at"]
    search_fields = ["offer__project_name", "entry__name"]
    list_filter = ["created_at"]
    readonly_fields = ["created_at", "updated_at", "deleted_at"]
    ordering = ["-created_at"]


@admin.register(OfferRate)
class OfferRateAdmin(admin.ModelAdmin):
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
class ShareAdmin(admin.ModelAdmin):
    """Share admin configuration."""

    list_display = ["offer", "type", "status", "created_at"]
    search_fields = ["offer__project_name", "link"]
    list_filter = ["type", "status", "created_at"]
    readonly_fields = ["created_at", "updated_at", "deleted_at"]
    ordering = ["-created_at"]
