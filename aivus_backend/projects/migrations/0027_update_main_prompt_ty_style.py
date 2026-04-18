"""Prepend the "always talk on 'ty'" style block to main_system_prompt.

Creates a v2 record for slug=main_system_prompt, deactivates v1. Reverse
migration restores v1 as active and deletes v2.
"""

from django.db import migrations

TY_STYLE_BLOCK = """\
СТИЛЬ ОБРАЩЕНИЯ:
- Всегда обращайся к клиенту на «ты», независимо от того, как пишет клиент.
- Не переходи на «вы» даже если клиент пишет на «вы» — это сразу создаёт
  дистанцию и ломает ощущение живого общения с продюсером-коллегой.
- Для нерусских языков используй самую неформальную форму обращения
  (английский you, испанский tú, французский tu, немецкий du и т.д.).

"""


def add_v2(apps, schema_editor):
    BriefPrompt = apps.get_model("projects", "BriefPrompt")
    active = (
        BriefPrompt.objects.filter(slug="main_system_prompt", is_active=True)
        .order_by("-version")
        .first()
    )
    if active is None:
        return

    new_body = TY_STYLE_BLOCK + active.body
    last_version = (
        BriefPrompt.objects.filter(slug="main_system_prompt")
        .order_by("-version")
        .first()
    )
    next_version = (last_version.version + 1) if last_version else 2

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


def revert_v2(apps, schema_editor):
    BriefPrompt = apps.get_model("projects", "BriefPrompt")
    v2 = (
        BriefPrompt.objects.filter(slug="main_system_prompt")
        .order_by("-version")
        .first()
    )
    if not v2 or v2.version < 2:
        return
    v2.delete()
    # Reactivate the newest remaining version
    last = (
        BriefPrompt.objects.filter(slug="main_system_prompt")
        .order_by("-version")
        .first()
    )
    if last:
        BriefPrompt.objects.filter(
            slug="main_system_prompt", is_active=True
        ).update(is_active=False)
        last.is_active = True
        last.save(update_fields=["is_active"])


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0026_seed_brief_prompts"),
    ]

    operations = [
        migrations.RunPython(add_v2, revert_v2),
    ]
