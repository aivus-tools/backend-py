# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0004_unit_is_default'),
    ]

    operations = [
        migrations.AddField(
            model_name='entry',
            name='short_description',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
    ]
