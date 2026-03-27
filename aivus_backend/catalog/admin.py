"""Django admin configuration for catalog app."""

from django import forms
from django.contrib import admin
from tinymce.widgets import TinyMCE
from unfold.admin import ModelAdmin

from .models import Category
from .models import Entry
from .models import EntryUnit
from .models import Unit


class EntryAdminForm(forms.ModelForm):
    """Custom form for Entry with TinyMCE WYSIWYG editor."""

    description = forms.CharField(
        widget=TinyMCE(attrs={"cols": 80, "rows": 20}),
        required=False,
    )

    class Meta:
        model = Entry
        fields = "__all__"  # noqa: DJ007


@admin.register(Category)
class CategoryAdmin(ModelAdmin):
    """Category admin configuration."""

    list_display = ["name", "code", "level", "parent_category", "tags", "created_at"]
    search_fields = ["name", "code"]
    list_filter = ["level", "tags", "created_at"]
    readonly_fields = ["created_at", "updated_at", "deleted_at"]
    ordering = ["level", "name"]


@admin.register(Unit)
class UnitAdmin(ModelAdmin):
    """Unit admin configuration."""

    list_display = ["name", "symbol", "dimension", "is_default", "created_at"]
    search_fields = ["name", "symbol"]
    list_filter = ["dimension", "is_default", "created_at"]
    readonly_fields = ["created_at", "updated_at", "deleted_at"]
    ordering = ["name"]


@admin.register(Entry)
class EntryAdmin(ModelAdmin):
    """Entry admin configuration."""

    form = EntryAdminForm
    list_display = [
        "name",
        "code",
        "short_description",
        "category",
        "is_approved",
        "created_at",
    ]
    search_fields = ["name", "code", "short_description", "description"]
    list_filter = ["is_approved", "category", "created_at"]
    readonly_fields = ["created_at", "updated_at", "deleted_at"]
    ordering = ["-created_at"]
    fields = [
        "name",
        "code",
        "category",
        "short_description",
        "description",
        "is_approved",
    ]


@admin.register(EntryUnit)
class EntryUnitAdmin(ModelAdmin):
    """EntryUnit admin configuration."""

    list_display = ["entry", "unit", "is_default", "created_at"]
    search_fields = ["entry__name", "unit__name"]
    list_filter = ["is_default", "created_at"]
    readonly_fields = ["created_at", "updated_at", "deleted_at"]
    ordering = ["-is_default", "unit__name"]
