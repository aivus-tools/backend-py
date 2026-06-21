from django.db import migrations, models
from django.utils import timezone


def backfill_email_confirmed_at(apps, schema_editor):
    User = apps.get_model("users", "User")
    now = timezone.now()
    for user in User.objects.all().iterator():
        if user.group == "UNCONFIRMED":
            user.email_confirmed_at = None
            user.group = "CONFIRMED"
        else:
            user.email_confirmed_at = user.created_at or now
        user.save(update_fields=["email_confirmed_at", "group"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0012_vendorwebhookkey"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="email_confirmed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_email_confirmed_at, noop_reverse),
    ]
