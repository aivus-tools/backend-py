"""Authentication API views."""

import json
import logging
import secrets
import string

from django.contrib.auth.hashers import make_password
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


def generate_temporary_password(length: int = 12) -> str:
    """Generate a secure temporary password."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))

logger = logging.getLogger(__name__)


@csrf_exempt
@require_http_methods(["POST"])
@public_endpoint
def register(request):
    """
    Register a new user.

    POST /api/v1/auth/register
    Body: {"email": "...", "password": "...", "name": "...", "authType": "CREDENTIALS"}
    """
    try:
        data = json.loads(request.body)
        email = data.get("email")
        password = data.get("password")
        name = data.get("name")
        auth_type = data.get("authType", "CREDENTIALS")

        if not email or not name:
            return JsonResponse({"error": "Email and name are required"}, status=400)

        # Check if user exists
        if User.objects.filter(email=email).exists():
            return JsonResponse({"error": "Email already exists"}, status=400)

        # For Google users, generate a temporary password
        if auth_type == "GOOGLE":
            temporary_password = generate_temporary_password()
            hashed_password = make_password(temporary_password)
        else:
            temporary_password = None
            hashed_password = make_password(password) if password else make_password("")

        # Create user
        user = User.objects.create(
            email=email,
            name=name,
            password=hashed_password,
            auth_type=auth_type,
            group="CONFIRMED" if auth_type == "GOOGLE" else "UNCONFIRMED",
        )

        # Send confirmation email for CREDENTIALS users
        if auth_type == "CREDENTIALS":
            token_obj = AuthToken.create_token(user, TokenType.EMAIL_CONFIRMATION)

            # Send confirmation email
            email_sent = send_confirmation_email(user, token_obj.token)

            if email_sent:
                logger.info("Confirmation email sent to %s", user.email)
            else:
                logger.error("Failed to send confirmation email to %s", user.email)

            return JsonResponse(
                {
                    "message": "User registered. Check your email to confirm account.",
                    "id": str(user.id),
                },
                status=201,
            )

        # Google users - send welcome email with temporary password
        email_sent = send_google_welcome_email(user, temporary_password)
        if email_sent:
            logger.info("Google welcome email sent to %s", user.email)
        else:
            logger.warning("Failed to send Google welcome email to %s", user.email)

        return JsonResponse(
            {
                "message": "User registered successfully via Google. Check your email for temporary password.",
                "id": str(user.id),
                "group": user.group,
            },
            status=201,
        )

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        logger.exception("Register error: %s", e)
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
@public_endpoint
def login(request):
    """
    Login user.

    POST /api/v1/auth/login
    Body: {"email": "...", "password": "...", "authType": "CREDENTIALS|GOOGLE"}
    """
    try:
        data = json.loads(request.body)
        email = data.get("email")
        password = data.get("password")
        auth_type = data.get("authType", "CREDENTIALS")

        logger.debug("Login attempt: email=%s, authType=%s", email, auth_type)

        if not email:
            return JsonResponse({"error": "Email is required"}, status=400)

        user = User.objects.filter(email=email).first()

        logger.debug(
            "User lookup: found=%s, user_auth_type=%s",
            bool(user),
            user.auth_type if user else None,
        )

        # If user doesn't exist, invalid credentials
        if not user:
            return JsonResponse({"error": "Invalid credentials"}, status=401)

        # Handle Google login - allow for any user
        if auth_type == "GOOGLE":
            # Google OAuth verified by NextAuth, prepare response data below
            pass
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
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
@require_http_methods(["GET"])
@public_endpoint
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
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
@public_endpoint
def check_email(request):
    """
    Check if email exists.

    POST /api/v1/auth/check-email
    Body: {"email": "..."}
    """
    try:
        data = json.loads(request.body)
        email = data.get("email")

        if not email:
            return JsonResponse({"error": "Email is required"}, status=400)

        user = User.objects.filter(email=email).first()

        return JsonResponse(
            {
                "exists": bool(user),
                "authType": user.auth_type if user else None,
            },
            status=200,
        )

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
@public_endpoint
def forgot_password(request):
    """
    Request password reset.

    POST /api/v1/auth/forgot-password
    Body: {"email": "..."}
    """
    try:
        data = json.loads(request.body)
        email = data.get("email")

        if not email:
            return JsonResponse({"error": "Email is required"}, status=400)

        user = User.objects.filter(email=email).first()

        if not user:
            return JsonResponse({"error": "User not found"}, status=404)

        token_obj = AuthToken.create_token(user, TokenType.PASSWORD_RESET)

        # Send password reset email
        email_sent = send_password_reset_email(user, token_obj.token)

        if email_sent:
            logger.info("Password reset email sent to %s", user.email)
        else:
            logger.error("Failed to send password reset email to %s", user.email)

        return JsonResponse(
            {
                "message": "Password reset link sent to your email.",
            },
            status=200,
        )

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
@public_endpoint
def resend_confirmation(request):
    """
    Resend email confirmation link.

    POST /api/v1/auth/resend-confirmation
    Body: {"email": "..."}
    """
    try:
        data = json.loads(request.body)
        email = data.get("email")

        if not email:
            return JsonResponse({"error": "Email is required"}, status=400)

        user = User.objects.filter(email=email).first()

        if not user:
            return JsonResponse({"error": "User not found"}, status=404)

        if user.group != "UNCONFIRMED":
            return JsonResponse(
                {"error": "User is already confirmed"},
                status=400,
            )

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
                "message": "Confirmation email has been resent.",
            },
            status=200,
        )

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
@public_endpoint
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
        user.password = make_password(new_password)
        user.save()

        token_obj.delete()

        return JsonResponse({"message": "Password reset successful"}, status=200)

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
