"""Data migration to fix "Dayы" unit name to "Days"."""

from django.db import migrations


def fix_day_unit_name(apps, schema_editor):
    Unit = apps.get_model("catalog", "Unit")
    Unit.objects.filter(name="Dayы").update(name="Days")


def revert_day_unit_name(apps, schema_editor):
    Unit = apps.get_model("catalog", "Unit")
    Unit.objects.filter(name="Days").update(name="Dayы")


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0006_alter_unit_is_default"),
    ]

    operations = [
        migrations.RunPython(fix_day_unit_name, revert_day_unit_name),
    ]
