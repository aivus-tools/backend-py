"""Email sending utilities."""

import logging
from urllib.parse import urlencode

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)


def send_confirmation_email(user, token: str) -> bool:
    """
    Send email confirmation link to user.

    Args:
        user: User instance
        token: Confirmation token string

    Returns:
        bool: True if email sent successfully
    """
    try:
        # Build confirmation URL
        # In production this would be your frontend URL
        frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")
        # Properly encode token for URL
        query_params = urlencode({"token": token})
        confirmation_url = f"{frontend_url}/auth/confirm-email?{query_params}"

        # Email subject
        subject = "Confirm your email - Aivus"

        # HTML email body
        html_message = render_to_string(
            "emails/confirm_email.html",
            {
                "user": user,
                "confirmation_url": confirmation_url,
                "frontend_url": frontend_url,
            },
        )

        # Plain text fallback
        plain_message = strip_tags(html_message)

        # Send email
        send_mail(
            subject=subject,
            message=plain_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            html_message=html_message,
            fail_silently=False,
        )

        logger.info("Confirmation email sent to %s", user.email)
        return True

    except Exception:
        logger.exception("Failed to send confirmation email to %s", user.email)
        return False


def send_password_reset_email(user, token: str) -> bool:
    """
    Send password reset link to user.

    Args:
        user: User instance
        token: Reset token string

    Returns:
        bool: True if email sent successfully
    """
    try:
        # Build reset URL
        frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")
        # Properly encode token for URL
        query_params = urlencode({"token": token})
        reset_url = f"{frontend_url}/auth/reset-password?{query_params}"

        # Email subject
        subject = "Reset your password - Aivus"

        # HTML email body
        html_message = render_to_string(
            "emails/reset_password.html",
            {
                "user": user,
                "reset_url": reset_url,
                "frontend_url": frontend_url,
            },
        )

        # Plain text fallback
        plain_message = strip_tags(html_message)

        # Send email
        send_mail(
            subject=subject,
            message=plain_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            html_message=html_message,
            fail_silently=False,
        )

        logger.info("Password reset email sent to %s", user.email)
        return True

    except Exception:
        logger.exception("Failed to send password reset email to %s", user.email)
        return False
