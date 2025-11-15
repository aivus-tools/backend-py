"""Django admin configuration for catalog app."""

from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import Category
from .models import Entry
from .models import EntryUnit
from .models import Unit


@admin.register(Category)
class CategoryAdmin(ModelAdmin):
    """Category admin configuration."""

    list_display = ["name", "level", "parent_category", "created_at"]
    search_fields = ["name"]
    list_filter = ["level", "created_at"]
    readonly_fields = ["created_at", "updated_at", "deleted_at"]
    ordering = ["level", "name"]


@admin.register(Unit)
class UnitAdmin(ModelAdmin):
    """Unit admin configuration."""

    list_display = ["name", "symbol", "dimension", "created_at"]
    search_fields = ["name", "symbol"]
    list_filter = ["dimension", "created_at"]
    readonly_fields = ["created_at", "updated_at", "deleted_at"]
    ordering = ["name"]


@admin.register(Entry)
class EntryAdmin(ModelAdmin):
    """Entry admin configuration."""

    list_display = ["name", "category", "is_approved", "created_at"]
    search_fields = ["name", "description"]
    list_filter = ["is_approved", "category", "created_at"]
    readonly_fields = ["created_at", "updated_at", "deleted_at"]
    ordering = ["-created_at"]


@admin.register(EntryUnit)
class EntryUnitAdmin(ModelAdmin):
    """EntryUnit admin configuration."""

    list_display = ["entry", "unit", "is_default", "created_at"]
    search_fields = ["entry__name", "unit__name"]
    list_filter = ["is_default", "created_at"]
    readonly_fields = ["created_at", "updated_at", "deleted_at"]
    ordering = ["-is_default", "unit__name"]
