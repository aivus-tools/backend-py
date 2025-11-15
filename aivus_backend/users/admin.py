"""Django admin configuration for users app."""

from django.contrib import admin
from django.contrib.auth import admin as auth_admin
from unfold.admin import ModelAdmin

from .forms import UserAdminChangeForm
from .forms import UserAdminCreationForm
from .models import Client
from .models import Team
from .models import User
from .models import UserTeam
from .models import Vendor


@admin.register(User)
class UserAdmin(ModelAdmin, auth_admin.UserAdmin):
    """User admin configuration."""

    form = UserAdminChangeForm
    add_form = UserAdminCreationForm
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("name", "position")}),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
            },
        ),
        (
            "Important dates",
            {
                "fields": (
                    "last_login",
                    "date_joined",
                    "created_at",
                    "updated_at",
                    "deleted_at",
                ),
            },
        ),
        ("Aivus", {"fields": ("group", "auth_type")}),
    )
    list_display = ["email", "name", "group", "is_superuser", "created_at"]
    search_fields = ["name", "email"]
    ordering = ["-created_at"]
    readonly_fields = ["created_at", "updated_at", "deleted_at"]
    list_filter = ["group", "auth_type", "is_staff", "is_superuser", "is_active"]


@admin.register(Client)
class ClientAdmin(ModelAdmin):
    """Client admin configuration."""

    list_display = ["name", "ein", "owner", "created_at"]
    search_fields = ["name", "ein", "owner__email", "owner__name"]
    list_filter = ["created_at"]
    readonly_fields = ["created_at", "updated_at", "deleted_at"]
    ordering = ["-created_at"]


@admin.register(Vendor)
class VendorAdmin(ModelAdmin):
    """Vendor admin configuration."""

    list_display = ["name", "owner", "created_at"]
    search_fields = ["name", "owner__email", "owner__name"]
    list_filter = ["created_at"]
    readonly_fields = ["created_at", "updated_at", "deleted_at"]
    ordering = ["-created_at"]


@admin.register(Team)
class TeamAdmin(ModelAdmin):
    """Team admin configuration."""

    list_display = ["name", "created_at"]
    search_fields = ["name"]
    list_filter = ["created_at"]
    readonly_fields = ["created_at", "updated_at", "deleted_at"]
    ordering = ["-created_at"]


@admin.register(UserTeam)
class UserTeamAdmin(ModelAdmin):
    """UserTeam admin configuration."""

    list_display = ["user", "team", "role", "created_at"]
    search_fields = ["user__name", "user__email", "team__name"]
    list_filter = ["role", "created_at"]
    readonly_fields = ["created_at", "updated_at", "deleted_at"]
    ordering = ["-created_at"]
