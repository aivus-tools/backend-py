"""Add BriefShare model for public sharing of finalized briefs."""

import secrets
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("projects", "0027_update_main_prompt_ty_style"),
    ]

    operations = [
        migrations.CreateModel(
            name="BriefShare",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "token",
                    models.CharField(
                        db_index=True,
                        default=secrets.token_urlsafe,
                        max_length=64,
                        unique=True,
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "brief",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="share",
                        to="projects.brief",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_brief_shares",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "brief_share",
                "ordering": ["-created_at"],
            },
        ),
    ]
