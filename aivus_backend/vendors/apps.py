"""Vendors app configuration."""

from django.apps import AppConfig


class VendorsConfig(AppConfig):
    """Vendors app configuration."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "aivus_backend.vendors"
    verbose_name = "Vendors"
