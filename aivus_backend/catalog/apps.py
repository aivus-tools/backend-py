"""Catalog app configuration."""

from django.apps import AppConfig


class CatalogConfig(AppConfig):
    """Catalog app configuration."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "aivus_backend.catalog"
    verbose_name = "Catalog"
