import logging
import smtplib

from anymail.exceptions import AnymailAPIError
from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from .models import User

logger = logging.getLogger(__name__)


@shared_task()
def get_users_count():
    """A pointless Celery task to demonstrate usage."""
    return User.objects.count()


@shared_task(
    bind=True,
    autoretry_for=(AnymailAPIError, smtplib.SMTPException),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    retry_kwargs={"max_retries": 5},
)
def send_templated_email(
    self,
    user_id: str,
    template: str,
    subject: str,
    context: dict,
) -> None:
    user = User.objects.filter(pk=user_id).first()
    if user is None:
        logger.warning("send_templated_email: user %s not found", user_id)
        return

    html_message = render_to_string(template, {"user": user, **context})
    plain_message = strip_tags(html_message)

    send_mail(
        subject=subject,
        message=plain_message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        html_message=html_message,
        fail_silently=False,
    )
    logger.info("Email %s sent to %s", template, user.email)
