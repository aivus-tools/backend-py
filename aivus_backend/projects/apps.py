"""Projects app configuration."""

from django.apps import AppConfig


class ProjectsConfig(AppConfig):
    """Projects app configuration."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "aivus_backend.projects"
    verbose_name = "Projects"
