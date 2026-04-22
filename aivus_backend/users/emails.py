"""Email sending utilities."""

import logging
from urllib.parse import urlencode

from django.conf import settings

from .tasks import send_templated_email

logger = logging.getLogger(__name__)


def send_confirmation_email(user, token: str) -> bool:
    frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")
    query_params = urlencode({"token": token})
    confirmation_url = f"{frontend_url}/auth/confirm-email?{query_params}"

    send_templated_email.delay(
        user_id=str(user.pk),
        template="emails/confirm_email.html",
        subject="Confirm your email - Aivus",
        context={
            "confirmation_url": confirmation_url,
            "frontend_url": frontend_url,
        },
    )
    logger.info("Queued confirmation email for %s", user.email)
    return True


def send_password_reset_email(user, token: str) -> bool:
    frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")
    query_params = urlencode({"token": token})
    reset_url = f"{frontend_url}/auth/reset-password?{query_params}"

    send_templated_email.delay(
        user_id=str(user.pk),
        template="emails/reset_password.html",
        subject="Reset your password - Aivus",
        context={
            "reset_url": reset_url,
            "frontend_url": frontend_url,
        },
    )
    logger.info("Queued password reset email for %s", user.email)
    return True
