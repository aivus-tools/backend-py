"""Prepend "keep chat reply short, don't dump the full brief" rules to the active main_system_prompt."""

from django.db import migrations

NO_DUMP_BLOCK = """\
ПРАВИЛА ДЛИНЫ ОТВЕТА:
- Когда ready_to_finalize=true, reply должен быть короткий (1-2 предложения): просто поздравь клиента и пригласи нажать кнопку «Finalize» в интерфейсе. Никаких секций, чек-листов, готового брифа в reply.
- Никогда не выкладывай готовый бриф, deliverables или оформленные заголовки/таблицы в reply — финальные документы генерируются отдельно и живут на вкладке Docs.
- В обычной беседе тоже держи reply компактным: одна мысль + один короткий вопрос. Длинные оформленные ответы в чат не подходят.

"""


def add_no_dump_version(apps, schema_editor):
    BriefPrompt = apps.get_model("projects", "BriefPrompt")
    active = (
        BriefPrompt.objects.filter(slug="main_system_prompt", is_active=True)
        .order_by("-version")
        .first()
    )
    if active is None:
        return

    new_body = NO_DUMP_BLOCK + active.body
    last_version = (
        BriefPrompt.objects.filter(slug="main_system_prompt")
        .order_by("-version")
        .first()
    )
    next_version = (last_version.version + 1) if last_version else 3

    BriefPrompt.objects.filter(slug="main_system_prompt", is_active=True).update(
        is_active=False
    )
    BriefPrompt.objects.create(
        slug="main_system_prompt",
        title=f"Main system prompt (v{next_version})",
        body=new_body,
        version=next_version,
        is_active=True,
        model_name=active.model_name,
        metadata=active.metadata,
    )


def revert_no_dump_version(apps, schema_editor):
    BriefPrompt = apps.get_model("projects", "BriefPrompt")
    latest = (
        BriefPrompt.objects.filter(slug="main_system_prompt")
        .order_by("-version")
        .first()
    )
    if not latest or latest.version < 3:
        return
    latest.delete()
    prev = (
        BriefPrompt.objects.filter(slug="main_system_prompt")
        .order_by("-version")
        .first()
    )
    if prev:
        BriefPrompt.objects.filter(
            slug="main_system_prompt", is_active=True
        ).update(is_active=False)
        prev.is_active = True
        prev.save(update_fields=["is_active"])


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0028_add_brief_share"),
    ]

    operations = [
        migrations.RunPython(add_no_dump_version, revert_no_dump_version),
    ]
