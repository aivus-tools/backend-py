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
from .models import VendorSettings


@admin.register(User)
class UserAdmin(ModelAdmin, auth_admin.UserAdmin):
    """User admin configuration."""

    form = UserAdminChangeForm
    add_form = UserAdminCreationForm
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "password1", "password2"),
            },
        ),
    )
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
    list_display = [
        "email",
        "name",
        "group",
        "is_superuser",
        "created_at",
        "deleted_at",
    ]
    search_fields = ["name", "email"]
    ordering = ["-created_at"]
    readonly_fields = ["created_at", "updated_at", "deleted_at"]
    list_filter = ["group", "auth_type", "is_staff", "is_superuser", "is_active"]

    def get_queryset(self, request):
        return self.model.objects.all_with_deleted()

    def get_deleted_objects(self, objs, request):
        """Return soft-cascade preview bypassing Django's Collector.

        Standard Collector trips over PROTECT on Client.owner / Vendor.owner
        and Project.vendor. Here we skip real-delete checks because
        User.delete() is soft and cascades via update(deleted_at=...).
        Already soft-deleted children are excluded from the preview because
        User.delete() is idempotent and will not touch them again.
        """
        user_ids = [user.id for user in objs]
        clients_by_owner: dict = {}
        for client in Client.objects.filter(
            owner_id__in=user_ids,
            deleted_at__isnull=True,
        ):
            clients_by_owner.setdefault(client.owner_id, []).append(client)
        vendors_by_owner: dict = {}
        for vendor in Vendor.objects.filter(
            owner_id__in=user_ids,
            deleted_at__isnull=True,
        ):
            vendors_by_owner.setdefault(vendor.owner_id, []).append(vendor)

        to_delete: list[str] = []
        model_count: dict[str, int] = {}
        user_label = str(User._meta.verbose_name_plural or "")  # noqa: SLF001
        client_label = str(Client._meta.verbose_name_plural or "")  # noqa: SLF001
        vendor_label = str(Vendor._meta.verbose_name_plural or "")  # noqa: SLF001
        for user in objs:
            to_delete.append(f"User: {user.email or user.id}")
            model_count[user_label] = model_count.get(user_label, 0) + 1

            clients = clients_by_owner.get(user.id, [])
            to_delete.extend(f"— Client: {client.name}" for client in clients)
            if clients:
                model_count[client_label] = model_count.get(client_label, 0) + len(
                    clients
                )

            vendors = vendors_by_owner.get(user.id, [])
            to_delete.extend(f"— Vendor: {vendor.name}" for vendor in vendors)
            if vendors:
                model_count[vendor_label] = model_count.get(vendor_label, 0) + len(
                    vendors
                )
        return to_delete, model_count, set(), []

    def delete_model(self, request, obj):
        obj.delete()

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            obj.delete()


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


@admin.register(VendorSettings)
class VendorSettingsAdmin(ModelAdmin):
    list_display = [
        "vendor",
        "company_name",
        "fringes_percent",
        "production_fee_percent",
        "created_at",
    ]
    search_fields = ["vendor__name", "company_name"]
    readonly_fields = ["created_at", "updated_at"]
    fieldsets = (
        (None, {"fields": ("vendor", "logo", "company_name", "agency_name")}),
        (
            "Production Defaults",
            {
                "fields": (
                    "fringes_percent",
                    "handling_percent",
                    "markup_percent",
                    "production_insurance_percent",
                    "production_fee_percent",
                )
            },
        ),
        (
            "Post-Production Defaults",
            {
                "fields": (
                    "post_markup_percent",
                    "post_insurance_percent",
                    "post_tax_percent",
                )
            },
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )
