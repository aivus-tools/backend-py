"""Admin registrations for email agent models."""

from django.contrib import admin
from unfold.admin import ModelAdmin

from aivus_backend.email_agent.models import ActionItem
from aivus_backend.email_agent.models import AgentLog
from aivus_backend.email_agent.models import EmailAccount
from aivus_backend.email_agent.models import EmailMessage
from aivus_backend.email_agent.models import EmailThread
from aivus_backend.email_agent.models import NotificationChannel
from aivus_backend.email_agent.models import NotificationLog
from aivus_backend.email_agent.models import OutboundDraft
from aivus_backend.email_agent.models import VendorAgentProfile


@admin.register(EmailAccount)
class EmailAccountAdmin(ModelAdmin):
    list_display = ("email", "role", "provider", "status", "vendor", "last_synced_at")
    list_filter = ("role", "provider", "status")
    search_fields = ("email",)
    readonly_fields = ("credential",)


@admin.register(EmailThread)
class EmailThreadAdmin(ModelAdmin):
    list_display = ("provider_thread_id", "state", "client_email", "vendor", "project")
    list_filter = ("state",)
    search_fields = ("provider_thread_id", "client_email", "canonical_subject")


@admin.register(EmailMessage)
class EmailMessageAdmin(ModelAdmin):
    list_display = (
        "provider_message_id",
        "direction",
        "intent",
        "from_email",
        "thread",
    )
    list_filter = ("direction", "intent", "is_auto_reply")
    search_fields = ("provider_message_id", "from_email", "subject")


@admin.register(ActionItem)
class ActionItemAdmin(ModelAdmin):
    list_display = ("text", "assignee", "status", "due_at", "thread")
    list_filter = ("assignee", "status")


@admin.register(AgentLog)
class AgentLogAdmin(ModelAdmin):
    list_display = ("event", "thread", "project", "created_at")
    list_filter = ("event",)


@admin.register(VendorAgentProfile)
class VendorAgentProfileAdmin(ModelAdmin):
    list_display = ("vendor", "autonomy_mode", "producer_email")
    list_filter = ("autonomy_mode",)


@admin.register(NotificationChannel)
class NotificationChannelAdmin(ModelAdmin):
    list_display = ("vendor", "type", "enabled")
    list_filter = ("type", "enabled")


@admin.register(NotificationLog)
class NotificationLogAdmin(ModelAdmin):
    list_display = ("event", "vendor", "delivered", "created_at")
    list_filter = ("event", "delivered")


@admin.register(OutboundDraft)
class OutboundDraftAdmin(ModelAdmin):
    list_display = ("kind", "status", "thread", "expires_at", "created_at")
    list_filter = ("kind", "status")
