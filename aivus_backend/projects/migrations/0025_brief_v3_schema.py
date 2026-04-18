"""Breaking change: v3 brief schema.

Wipes existing AI brief data (briefs, chat messages, feedback, shares,
methodology) and restructures Brief for the new conversational flow:
- drops HTML section storage (document_sections, sections_status, archetypes,
  questions_asked, conversation_phase, version)
- adds title, document_language, conversation_status
- introduces BriefPrompt (editable system prompts), BriefAttachment (GCS
  files for multimodal input) and BriefFinalDocument (3 deliverables)
- makes LLMCallTrace.message nullable and adds final_document FK
- removes BriefShare and BriefMethodology models entirely
"""

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations
from django.db import models

from aivus_backend.projects.models import _brief_attachment_upload_to


def wipe_brief_data(apps, schema_editor):
    LLMCallTrace = apps.get_model("projects", "LLMCallTrace")
    BriefFeedback = apps.get_model("projects", "BriefFeedback")
    ChatMessage = apps.get_model("projects", "ChatMessage")
    BriefOffer = apps.get_model("projects", "BriefOffer")
    BriefShare = apps.get_model("projects", "BriefShare")
    BriefMethodology = apps.get_model("projects", "BriefMethodology")
    Brief = apps.get_model("projects", "Brief")

    LLMCallTrace.objects.all().delete()
    BriefFeedback.objects.all().delete()
    ChatMessage.objects.all().delete()
    BriefOffer.objects.all().delete()
    BriefShare.objects.all().delete()
    BriefMethodology.objects.all().delete()
    Brief.objects.all().delete()


def reverse_noop(apps, schema_editor):
    raise RuntimeError("Irreversible breaking change")


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("projects", "0024_llm_call_trace"),
    ]

    operations = [
        migrations.RunPython(wipe_brief_data, reverse_noop),
        # Brief model reshape
        migrations.RemoveField(model_name="brief", name="document_sections"),
        migrations.RemoveField(model_name="brief", name="archetypes"),
        migrations.RemoveField(model_name="brief", name="sections_status"),
        migrations.RemoveField(model_name="brief", name="questions_asked"),
        migrations.RemoveField(model_name="brief", name="conversation_phase"),
        migrations.RemoveField(model_name="brief", name="version"),
        migrations.AddField(
            model_name="brief",
            name="title",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="brief",
            name="document_language",
            field=models.CharField(blank=True, default="", max_length=10),
        ),
        migrations.AddField(
            model_name="brief",
            name="conversation_status",
            field=models.CharField(
                choices=[
                    ("in_progress", "In Progress"),
                    ("ready_to_finalize", "Ready to Finalize"),
                    ("finalized", "Finalized"),
                ],
                default="in_progress",
                max_length=20,
            ),
        ),
        # ChatMessage reshape
        migrations.RemoveField(model_name="chatmessage", name="sections_changed"),
        migrations.AddField(
            model_name="chatmessage",
            name="ready_to_finalize",
            field=models.BooleanField(default=False),
        ),
        # BriefFeedback reshape (drop section_key, require message FK)
        migrations.RemoveField(model_name="brieffeedback", name="section_key"),
        migrations.AlterField(
            model_name="brieffeedback",
            name="message",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="feedbacks",
                to="projects.chatmessage",
            ),
        ),
        migrations.AlterField(
            model_name="brieffeedback",
            name="user",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="brief_feedbacks",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        # LLMCallTrace: make message nullable and add final_document FK
        migrations.AlterField(
            model_name="llmcalltrace",
            name="message",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="llm_traces",
                to="projects.chatmessage",
            ),
        ),
        # Drop legacy models
        migrations.DeleteModel(name="BriefShare"),
        migrations.DeleteModel(name="BriefMethodology"),
        # New models
        migrations.CreateModel(
            name="BriefPrompt",
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
                    "slug",
                    models.CharField(
                        choices=[
                            ("main_system_prompt", "Main system prompt"),
                            ("finalization_prompt", "Finalization prompt"),
                            ("master_brief_template", "Master brief template"),
                            ("archetypes_reference", "Archetypes reference"),
                        ],
                        db_index=True,
                        max_length=64,
                    ),
                ),
                ("title", models.CharField(max_length=255)),
                ("body", models.TextField()),
                ("version", models.IntegerField(default=1)),
                ("is_active", models.BooleanField(db_index=True, default=False)),
                (
                    "model_name",
                    models.CharField(blank=True, default="", max_length=100),
                ),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_brief_prompts",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "brief_prompt",
                "ordering": ["slug", "-version"],
            },
        ),
        migrations.AddConstraint(
            model_name="briefprompt",
            constraint=models.UniqueConstraint(
                fields=("slug", "version"),
                name="brief_prompt_slug_version_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="briefprompt",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_active", True)),
                fields=("slug",),
                name="brief_prompt_active_single",
            ),
        ),
        migrations.CreateModel(
            name="BriefFinalDocument",
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
                    "kind",
                    models.CharField(
                        choices=[
                            ("production_brief", "Production Brief"),
                            ("vendor_email", "Vendor Outreach Email"),
                            (
                                "deliverables_checklist",
                                "Deliverables Checklist",
                            ),
                        ],
                        max_length=32,
                    ),
                ),
                ("html", models.TextField(blank=True, default="")),
                ("plain_text", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "brief",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="final_documents",
                        to="projects.brief",
                    ),
                ),
            ],
            options={
                "db_table": "brief_final_document",
                "ordering": ["brief_id", "kind"],
            },
        ),
        migrations.AddConstraint(
            model_name="brieffinaldocument",
            constraint=models.UniqueConstraint(
                fields=("brief", "kind"),
                name="brief_final_document_unique_kind",
            ),
        ),
        migrations.AddField(
            model_name="llmcalltrace",
            name="final_document",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="llm_traces",
                to="projects.brieffinaldocument",
            ),
        ),
        migrations.CreateModel(
            name="BriefAttachment",
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
                    "file",
                    models.FileField(upload_to=_brief_attachment_upload_to),
                ),
                ("filename", models.CharField(max_length=255)),
                ("mime_type", models.CharField(max_length=128)),
                ("size_bytes", models.BigIntegerField(default=0)),
                (
                    "gemini_file_uri",
                    models.CharField(blank=True, default="", max_length=512),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "brief",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="attachments",
                        to="projects.brief",
                    ),
                ),
                (
                    "message",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="attachments",
                        to="projects.chatmessage",
                    ),
                ),
            ],
            options={
                "db_table": "brief_attachment",
                "ordering": ["created_at"],
            },
        ),
    ]
