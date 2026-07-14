from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('email_agent', '0003_thread_memory'),
    ]

    operations = [
        migrations.AddField(
            model_name='actionitem',
            name='followup_count',
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='actionitem',
            name='last_followup_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='emailthread',
            name='state_before_pause',
            field=models.CharField(
                blank=True,
                choices=[
                    ('monitoring', 'Monitoring'),
                    ('engaged', 'Engaged'),
                    ('paused', 'Paused'),
                    ('human_takeover', 'Human takeover'),
                ],
                default='',
                max_length=16,
            ),
        ),
        migrations.AddIndex(
            model_name='actionitem',
            index=models.Index(
                fields=['assignee', 'status', 'due_at'],
                name='email_agent_assigne_a3f130_idx',
            ),
        ),
    ]
