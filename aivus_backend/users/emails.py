"""Email sending utilities."""
# ruff: noqa: RUF001

import logging
from urllib.parse import urlencode

from django.conf import settings

from .i18n import user_language
from .tasks import send_templated_email

logger = logging.getLogger(__name__)

SUBJECTS = {
    "confirm_email": {
        "en": "Confirm your email - Aivus",
        "ru": "Подтвердите email - Aivus",
    },
    "reset_password": {
        "en": "Reset your password - Aivus",
        "ru": "Сброс пароля - Aivus",
    },
}


def _subject(key: str, language: str) -> str:
    translations = SUBJECTS[key]
    return translations.get(language) or translations["en"]


def send_confirmation_email(user, token: str) -> bool:
    language = user_language(user)
    frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")
    query_params = urlencode({"token": token})
    confirmation_url = f"{frontend_url}/auth/confirm-email?{query_params}"

    send_templated_email.delay(
        user_id=str(user.pk),
        template=f"emails/confirm_email_{language}.html",
        subject=_subject("confirm_email", language),
        context={
            "confirmation_url": confirmation_url,
            "frontend_url": frontend_url,
        },
    )
    logger.info("Queued confirmation email for %s (lang=%s)", user.email, language)
    return True


def send_password_reset_email(user, token: str) -> bool:
    language = user_language(user)
    frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")
    query_params = urlencode({"token": token})
    reset_url = f"{frontend_url}/auth/reset-password?{query_params}"

    send_templated_email.delay(
        user_id=str(user.pk),
        template=f"emails/reset_password_{language}.html",
        subject=_subject("reset_password", language),
        context={
            "reset_url": reset_url,
            "frontend_url": frontend_url,
        },
    )
    logger.info("Queued password reset email for %s (lang=%s)", user.email, language)
    return True
