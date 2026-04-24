"""Django admin configuration for users app."""

from datetime import timedelta

from django.contrib import admin
from django.contrib.auth import admin as auth_admin
from django.db.models import Count
from django.db.models.functions import TruncDate
from django.utils import timezone
from unfold.admin import ModelAdmin

from .forms import UserAdminChangeForm
from .forms import UserAdminCreationForm
from .models import Client
from .models import Team
from .models import User
from .models import UserTeam
from .models import Vendor
from .models import VendorSettings


class DeletedStateFilter(admin.SimpleListFilter):
    """Default to alive records; allow browsing soft-deleted ones explicitly."""

    title = "state"
    parameter_name = "state"

    def lookups(self, request, model_admin):
        return (
            ("alive", "Alive"),
            ("deleted", "Deleted"),
            ("all", "All"),
        )

    def choices(self, changelist):
        yield {
            "selected": self.value() is None or self.value() == "alive",
            "query_string": changelist.get_query_string(remove=[self.parameter_name]),
            "display": "Alive",
        }
        for lookup, title in (("deleted", "Deleted"), ("all", "All")):
            yield {
                "selected": self.value() == lookup,
                "query_string": changelist.get_query_string(
                    {self.parameter_name: lookup}
                ),
                "display": title,
            }

    def queryset(self, request, queryset):
        value = self.value()
        if value == "deleted":
            return queryset.filter(deleted_at__isnull=False)
        if value == "all":
            return queryset
        return queryset.filter(deleted_at__isnull=True)


def _compute_user_stats() -> dict:
    """Aggregate metrics for the UserAdmin changelist dashboard.

    All counts exclude soft-deleted users. Daily series is filled with zeros
    so the chart always covers the full 30-day window even if there are gaps.
    """
    qs = User.objects.filter(deleted_at__isnull=True)
    now = timezone.now()
    today = now.date()
    days_back = 30
    since_dt = now - timedelta(days=days_back - 1)
    since_day = since_dt.date()

    daily_rows = (
        qs.filter(created_at__date__gte=since_day)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(count=Count("id"))
        .order_by("day")
    )
    by_day = {row["day"]: row["count"] for row in daily_rows}
    daily_series = []
    for offset in range(days_back):
        day = since_day + timedelta(days=offset)
        daily_series.append({"date": day.isoformat(), "count": by_day.get(day, 0)})

    new_7d = sum(item["count"] for item in daily_series[-7:])
    new_30d = sum(item["count"] for item in daily_series)
    prev_7d_start = today - timedelta(days=13)
    prev_7d_end = today - timedelta(days=7)
    prev_7d = qs.filter(
        created_at__date__gte=prev_7d_start,
        created_at__date__lt=prev_7d_end,
    ).count()
    if prev_7d:
        growth_7d = round(((new_7d - prev_7d) / prev_7d) * 100, 1)
    elif new_7d:
        growth_7d = 100.0
    else:
        growth_7d = 0.0

    total = qs.count()
    deleted = User.objects.filter(deleted_at__isnull=False).count()
    active_7d = qs.filter(last_login__gte=now - timedelta(days=7)).count()

    by_group_rows = qs.values("group").annotate(count=Count("id")).order_by("-count")
    by_group = [
        {"label": row["group"] or "unset", "count": row["count"]}
        for row in by_group_rows
    ]

    by_auth_rows = qs.values("auth_type").annotate(count=Count("id")).order_by("-count")
    by_auth = [
        {"label": row["auth_type"] or "unset", "count": row["count"]}
        for row in by_auth_rows
    ]

    return {
        "total": total,
        "deleted": deleted,
        "new_7d": new_7d,
        "new_30d": new_30d,
        "growth_7d": growth_7d,
        "active_7d": active_7d,
        "daily": daily_series,
        "by_group": by_group,
        "by_auth": by_auth,
    }


@admin.register(User)
class UserAdmin(ModelAdmin, auth_admin.UserAdmin):
    """User admin configuration."""

    change_list_template = "admin/users/user/change_list.html"

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
    list_filter = [
        DeletedStateFilter,
        "group",
        "auth_type",
        "is_staff",
        "is_superuser",
        "is_active",
    ]

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        try:
            extra_context["user_stats"] = _compute_user_stats()
        except Exception:
            extra_context["user_stats"] = None
        return super().changelist_view(request, extra_context=extra_context)

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
