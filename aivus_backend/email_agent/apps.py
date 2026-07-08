"""Email agent app configuration (Stage 3: Email PM Agent & mini-CRM)."""

from django.apps import AppConfig


class EmailAgentConfig(AppConfig):
    """Email agent app configuration."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "aivus_backend.email_agent"
    verbose_name = "Email agent"
