"""Django admin configuration for vendors app."""

from django.contrib import admin
from django.utils.html import format_html
from unfold.admin import ModelAdmin

from .models import PreVendor


@admin.register(PreVendor)
class PreVendorAdmin(ModelAdmin):
    """PreVendor admin configuration."""

    list_display = [
        "title",
        "language",
        "rank_label",
        "email",
        "sort_order",
        "logo_preview",
        "created_at",
    ]
    list_editable = ["sort_order"]
    list_display_links = ["title"]
    list_filter = ["language", "created_at"]
    search_fields = ["title", "email", "short_description", "address", "category_label"]
    readonly_fields = ["created_at", "updated_at", "deleted_at", "logo_preview"]
    ordering = ["sort_order", "-created_at"]
    fieldsets = (
        (
            "Content",
            {
                "fields": (
                    "language",
                    "title",
                    "short_description",
                    "rank_label",
                    "category_label",
                    "logo",
                    "logo_preview",
                    "portfolio_url",
                ),
            },
        ),
        ("Contact", {"fields": ("email", "address")}),
        ("Display", {"fields": ("sort_order",)}),
        (
            "System",
            {"fields": ("created_at", "updated_at", "deleted_at")},
        ),
    )

    @admin.display(description="Logo preview")
    def logo_preview(self, obj):
        if obj and obj.logo:
            style = "height:32px;max-width:120px;object-fit:contain"
            return format_html('<img src="{}" style="{}"/>', obj.logo.url, style)
        return "—"

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        last = self._latest_pre_vendor()
        if last is not None:
            initial.setdefault("rank_label", last.rank_label)
            initial.setdefault("category_label", last.category_label)
        return initial

    def save_model(self, request, obj, form, change):
        if not change and not obj.logo:
            last = self._latest_pre_vendor()
            if last is not None and last.logo:
                obj.logo = last.logo.name
        super().save_model(request, obj, form, change)

    @staticmethod
    def _latest_pre_vendor():
        return (
            PreVendor.objects.filter(deleted_at__isnull=True)
            .order_by("-created_at")
            .first()
        )
