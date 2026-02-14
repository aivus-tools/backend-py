"""Authentication API views."""

import json
import logging
import secrets
import string

from django.contrib.auth.hashers import make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from aivus_backend.core.decorators import public_endpoint
from aivus_backend.users.emails import send_confirmation_email
from aivus_backend.users.emails import send_google_welcome_email
from aivus_backend.users.emails import send_password_reset_email
from aivus_backend.users.models import User, Vendor, Client
from aivus_backend.users.tokens import AuthToken
from aivus_backend.users.tokens import TokenType

try:
    from django_ratelimit.decorators import ratelimit
except ImportError:
    from django.conf import settings as django_settings

    if not django_settings.DEBUG:
        raise ImportError(
            "django-ratelimit is required in production but not installed. "
            "Run: pip install django-ratelimit"
        )

    # Fallback: no-op decorator only in DEBUG mode
    def ratelimit(**kwargs):  # noqa: ARG001
        def decorator(func):
            return func
        return decorator


def generate_temporary_password(length: int = 12) -> str:
    """Generate a secure temporary password."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))

logger = logging.getLogger(__name__)


@csrf_exempt
@require_http_methods(["POST"])
@public_endpoint
@ratelimit(key="ip", rate="5/m", method="POST", block=True)
def register(request):
    """
    Register a new user.

    POST /api/v1/auth/register
    Body: {"email": "...", "password": "...", "name": "...", "authType": "CREDENTIALS"}
    """
    try:
        data = json.loads(request.body)
        email = data.get("email", "").strip().lower()
        password = data.get("password")
        name = data.get("name")
        auth_type = data.get("authType", "CREDENTIALS")

        if not email or not name:
            return JsonResponse({"error": "Email and name are required"}, status=400)

        # QA2-001: Reject Google auth type through register endpoint
        if auth_type == "GOOGLE":
            return JsonResponse(
                {"error": "Google authentication must use OAuth flow"},
                status=400,
            )

        # Check if user exists
        if User.objects.filter(email=email).exists():
            return JsonResponse({"error": "Email already exists"}, status=400)

        # QA2-012: Use Django's password validation instead of simple length check
        if not password:
            return JsonResponse(
                {"error": "Password is required"},
                status=400,
            )
        try:
            validate_password(password)
        except ValidationError as e:
            return JsonResponse(
                {"error": e.messages},
                status=400,
            )
        hashed_password = make_password(password)

        # Create user
        user = User.objects.create(
            email=email,
            name=name,
            password=hashed_password,
            auth_type=auth_type,
            group="UNCONFIRMED",
        )

        # Send confirmation email for CREDENTIALS users
        token_obj = AuthToken.create_token(user, TokenType.EMAIL_CONFIRMATION)

        # Send confirmation email
        email_sent = send_confirmation_email(user, token_obj.token)

        if email_sent:
            logger.info("Confirmation email sent to %s", user.email)
        else:
            logger.error("Failed to send confirmation email to %s", user.email)

        # QA4-049: Do not expose internal UUID in register response
        return JsonResponse(
            {
                "message": "User registered. Check your email to confirm account.",
            },
            status=201,
        )

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        logger.exception("Register error: %s", e)
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
@public_endpoint
@ratelimit(key="ip", rate="5/m", method="POST", block=True)
def login(request):
    """
    Login user.

    POST /api/v1/auth/login
    Body: {"email": "...", "password": "...", "authType": "CREDENTIALS|GOOGLE"}
    """
    try:
        data = json.loads(request.body)
        email = data.get("email", "").strip().lower()
        password = data.get("password")
        auth_type = data.get("authType", "CREDENTIALS")

        logger.debug("Login attempt: email=%s, authType=%s", email, auth_type)

        if not email:
            return JsonResponse({"error": "Email is required"}, status=400)

        # QA4-009: Exclude soft-deleted users from login
        user = User.objects.filter(email=email, deleted_at__isnull=True).first()

        logger.debug(
            "User lookup: found=%s, user_auth_type=%s",
            bool(user),
            user.auth_type if user else None,
        )

        # If user doesn't exist, invalid credentials
        if not user:
            return JsonResponse({"error": "Invalid credentials"}, status=401)

        # Google authentication must go through OAuth flow, not direct login
        if auth_type == "GOOGLE":
            return JsonResponse(
                {"error": "Google authentication must go through OAuth flow, not direct login"},
                status=400,
            )
        else:
            # Handle credential login - require password
            if not password:
                logger.debug("No password provided for credential login")
                return JsonResponse(
                    {"error": "Password is required"},
                    status=400,
                )
            logger.debug("Checking password for user: %s", user.email)

            password_valid = user.check_plain_password(password)
            logger.debug("Password validation result: %s", password_valid)

            if not password_valid:
                return JsonResponse({"error": "Invalid credentials"}, status=401)

        # QA4-010: Block UNCONFIRMED users from logging in
        if user.group == "UNCONFIRMED":
            return JsonResponse(
                {"error": "Please confirm your email before logging in"},
                status=403,
            )

        # Prepare common response data
        response_data = {
            "id": str(user.id),
            "email": user.email,
            "name": user.name,
            "group": user.group,
        }

        # Add vendor_id or client_id if applicable
        if user.group == "VENDOR":
            vendor = Vendor.objects.filter(owner=user).first()
            if vendor:
                response_data["vendorId"] = str(vendor.id)
        elif user.group == "CLIENT":
            client = Client.objects.filter(owner=user).first()
            if client:
                response_data["clientId"] = str(client.id)

        return JsonResponse(response_data, status=200)

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        logger.exception("Login error: %s", e)
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@csrf_exempt
@require_http_methods(["GET"])
@public_endpoint
@ratelimit(key="ip", rate="10/m", method="GET", block=True)
def confirm_email(request):
    """
    Confirm email.

    GET /api/v1/auth/confirm-email?token=...
    """
    token = request.GET.get("token")
    logger.debug("Confirming email with token: %s", token)

    if not token:
        return JsonResponse({"error": "Token is required"}, status=400)

    try:
        token_obj = AuthToken.objects.filter(
            token=token,
            token_type=TokenType.EMAIL_CONFIRMATION,
        ).first()

        if not token_obj or not token_obj.is_valid():
            logger.warning("Invalid or expired token: %s", token)
            return JsonResponse(
                {"error": "Invalid or expired confirmation token"},
                status=400,
            )

        user = token_obj.user

        # QA4-033: Only allow confirming UNCONFIRMED users
        if user.group != "UNCONFIRMED":
            return JsonResponse(
                {"error": "Email already confirmed"},
                status=400,
            )

        user.group = "CONFIRMED"
        user.save()
        logger.info("Email confirmed for user: %s", user.email)

        token_obj.delete()

        return JsonResponse(
            {
                "id": str(user.id),
                "email": user.email,
                "name": user.name,
                "group": user.group,
            },
            status=200,
        )

    except Exception as e:
        logger.exception("Confirm email error: %s", e)
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
@public_endpoint
@ratelimit(key="ip", rate="10/m", method="POST", block=True)
def check_email(request):
    """
    Check if email exists.

    POST /api/v1/auth/check-email
    Body: {"email": "..."}

    QA2-011: Always returns exists=true to prevent email enumeration.
    QA3-059: This endpoint intentionally always returns true for security
    (anti-enumeration). Kept for frontend backward compatibility.
    """
    try:
        data = json.loads(request.body)
        email = data.get("email", "").strip().lower()

        if not email:
            return JsonResponse({"error": "Email is required"}, status=400)

        # QA2-011: Always return exists=true to prevent email enumeration
        return JsonResponse(
            {
                "exists": True,
            },
            status=200,
        )

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        logger.exception("Check email error: %s", e)
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
@public_endpoint
@ratelimit(key="ip", rate="5/m", method="POST", block=True)
def forgot_password(request):
    """
    Request password reset.

    POST /api/v1/auth/forgot-password
    Body: {"email": "..."}
    """
    try:
        data = json.loads(request.body)
        email = data.get("email", "").strip().lower()

        if not email:
            return JsonResponse({"error": "Email is required"}, status=400)

        # QA4-009: Exclude soft-deleted users from password reset
        user = User.objects.filter(email=email, deleted_at__isnull=True).first()

        if user:
            token_obj = AuthToken.create_token(user, TokenType.PASSWORD_RESET)

            # Send password reset email
            email_sent = send_password_reset_email(user, token_obj.token)

            if email_sent:
                logger.info("Password reset email sent to %s", user.email)
            else:
                logger.error("Failed to send password reset email to %s", user.email)

        return JsonResponse(
            {
                "message": "If an account with that email exists, a password reset link has been sent.",
            },
            status=200,
        )

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        logger.exception("Forgot password error: %s", e)
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
@public_endpoint
@ratelimit(key="ip", rate="3/m", method="POST", block=True)
def resend_confirmation(request):
    """
    Resend email confirmation link.

    POST /api/v1/auth/resend-confirmation
    Body: {"email": "..."}
    """
    try:
        data = json.loads(request.body)
        email = data.get("email", "").strip().lower()

        if not email:
            return JsonResponse({"error": "Email is required"}, status=400)

        # QA3-013: Always return generic 200 to prevent email enumeration
        # QA4-009: Exclude soft-deleted users
        user = User.objects.filter(email=email, deleted_at__isnull=True).first()

        if user and user.group == "UNCONFIRMED":
            # Delete old confirmation tokens
            AuthToken.objects.filter(
                user=user,
                token_type=TokenType.EMAIL_CONFIRMATION,
            ).delete()

            # Create new token
            token_obj = AuthToken.create_token(user, TokenType.EMAIL_CONFIRMATION)

            # Send confirmation email
            email_sent = send_confirmation_email(user, token_obj.token)

            if email_sent:
                logger.info("Confirmation email resent to %s", user.email)
            else:
                logger.error("Failed to resend confirmation email to %s", user.email)

        return JsonResponse(
            {
                "message": "If an account with that email exists and is unconfirmed, a confirmation email has been sent.",
            },
            status=200,
        )

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        logger.exception("Resend confirmation error: %s", e)
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
@public_endpoint
@ratelimit(key="ip", rate="5/m", method="POST", block=True)
def reset_password(request):
    """
    Reset password.

    POST /api/v1/auth/reset-password?token=...
    Body: {"password": "..."}
    """
    token = request.GET.get("token")

    if not token:
        return JsonResponse({"error": "Token is required"}, status=400)

    try:
        data = json.loads(request.body)
        new_password = data.get("password")

        if not new_password:
            return JsonResponse({"error": "Password is required"}, status=400)

        # QA2-012: Use Django's password validation instead of simple length check
        try:
            validate_password(new_password)
        except ValidationError as e:
            return JsonResponse(
                {"error": e.messages},
                status=400,
            )

        token_obj = AuthToken.objects.filter(
            token=token,
            token_type=TokenType.PASSWORD_RESET,
        ).first()

        if not token_obj or not token_obj.is_valid():
            return JsonResponse(
                {"error": "Invalid or expired reset token"},
                status=400,
            )

        user = token_obj.user
        # QA2-004: Use set_password() to change session auth hash,
        # invalidating all other sessions on next request
        user.set_password(new_password)
        user.save()

        token_obj.delete()

        return JsonResponse({"message": "Password reset successful"}, status=200)

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        logger.exception("Reset password error: %s", e)
        return JsonResponse({"error": "An internal error occurred"}, status=500)
