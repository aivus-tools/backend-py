from allauth.account.models import EmailAddress
from allauth.mfa.models import Authenticator
from allauth.socialaccount.models import SocialAccount
from allauth.socialaccount.models import SocialApp
from allauth.socialaccount.models import SocialToken
from django.conf import settings
from django.contrib import admin
from django.contrib.auth.models import Group
from django.contrib.sites.models import Site
from django_celery_beat.models import ClockedSchedule
from django_celery_beat.models import CrontabSchedule
from django_celery_beat.models import IntervalSchedule
from django_celery_beat.models import PeriodicTask
from django_celery_beat.models import SolarSchedule

admin.site.unregister(Group)
admin.site.unregister(Site)

admin.site.unregister(EmailAddress)
admin.site.unregister(Authenticator)
admin.site.unregister(SocialAccount)
admin.site.unregister(SocialApp)
admin.site.unregister(SocialToken)

admin.site.unregister(ClockedSchedule)
admin.site.unregister(CrontabSchedule)
admin.site.unregister(IntervalSchedule)
admin.site.unregister(PeriodicTask)
admin.site.unregister(SolarSchedule)


def admin_environment_callback(request):
    commit = getattr(settings, "GIT_COMMIT", "dev")
    return [f"build {commit[:7]}", "info"]
