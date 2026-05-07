from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0032_seed_stt_industry_terms"),
    ]

    operations = [
        migrations.AlterField(
            model_name="briefprompt",
            name="slug",
            field=models.CharField(
                choices=[
                    ("main_system_prompt", "Main system prompt"),
                    ("finalization_prompt", "Finalization prompt"),
                    ("master_brief_template", "Master brief template"),
                    ("archetypes_reference", "Archetypes reference"),
                    ("stt_industry_terms", "STT industry terms"),
                ],
                db_index=True,
                max_length=64,
            ),
        ),
    ]
