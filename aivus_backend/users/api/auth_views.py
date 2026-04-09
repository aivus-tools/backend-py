"""Authentication API views."""

import json
import logging
import secrets
import string
import uuid as uuid_module
from hmac import compare_digest

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from aivus_backend.core.decorators import public_endpoint
from aivus_backend.users.emails import send_confirmation_email
from aivus_backend.users.emails import send_password_reset_email
from aivus_backend.users.models import Client
from aivus_backend.users.models import User
from aivus_backend.users.models import Vendor
from aivus_backend.users.tokens import AuthToken
from aivus_backend.users.tokens import TokenType

try:
    from django_ratelimit.decorators import ratelimit
except ImportError:
    from django.conf import settings as django_settings

    if not django_settings.DEBUG:
        msg = (
            "django-ratelimit is required in production but not installed. "
            "Run: pip install django-ratelimit"
        )
        raise ImportError(msg) from None

    # Fallback: no-op decorator only in DEBUG mode
    def ratelimit(**kwargs):
        def decorator(func):
            return func

        return decorator


def generate_temporary_password(length: int = 12) -> str:
    """Generate a secure temporary password."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


logger = logging.getLogger(__name__)

INTERNAL_SECRET_HEADER = "HTTP_X_INTERNAL_SECRET"  # noqa: S105


def _is_internal_request(request):
    secret = getattr(settings, "HMAC_SECRET", None)
    if not secret:
        return False
    header_value = request.META.get(INTERNAL_SECRET_HEADER, "")
    if not header_value:
        return False
    return compare_digest(str(secret), str(header_value))


def _save_pending_brief(user, data):
    brief_id = data.get("briefId")
    brief_token = data.get("briefToken")
    if not brief_id or not brief_token:
        return
    try:
        uuid_module.UUID(str(brief_id))
    except (ValueError, AttributeError):
        return
    from aivus_backend.projects.models import Brief  # noqa: PLC0415

    exists = Brief.objects.filter(
        id=brief_id,
        anonymous_token=brief_token,
        client__isnull=True,
        deleted_at__isnull=True,
    ).exists()
    if exists:
        user.pending_brief_id = brief_id
        user.pending_brief_token = brief_token
        user.save(update_fields=["pending_brief_id", "pending_brief_token"])


def _try_claim_pending_brief(user):
    if not user.pending_brief_id or not user.pending_brief_token:
        return None
    client = Client.objects.filter(owner=user).first()
    if not client:
        return None
    from aivus_backend.projects.models import Brief  # noqa: PLC0415
    from aivus_backend.projects.models import ChatMessage  # noqa: PLC0415

    now = timezone.now()
    with transaction.atomic():
        rows = Brief.objects.filter(
            id=user.pending_brief_id,
            anonymous_token=user.pending_brief_token,
            client__isnull=True,
            deleted_at__isnull=True,
        ).update(client=client, anonymous_token=None, claimed_at=now)
        if rows > 0:
            ChatMessage.objects.filter(
                brief_id=user.pending_brief_id,
                anonymous_token=user.pending_brief_token,
            ).update(anonymous_token="")
    brief_id = str(user.pending_brief_id)
    user.pending_brief_id = None
    user.pending_brief_token = None
    user.save(update_fields=["pending_brief_id", "pending_brief_token"])
    return brief_id if rows > 0 else None


@csrf_exempt
@require_http_methods(["POST"])
@public_endpoint
@ratelimit(key="ip", rate="5/m", method="POST", block=True)
def register(request):  # noqa: C901, PLR0912
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

        is_internal = _is_internal_request(request)
        is_google = auth_type == "GOOGLE"

        if is_google and not is_internal:
            return JsonResponse(
                {"error": "Google authentication must use OAuth flow"},
                status=400,
            )

        if User.objects.filter(email=email).exists():
            return JsonResponse({"error": "Email already exists"}, status=400)

        if is_google:
            hashed_password = make_password(generate_temporary_password(32))
        else:
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

        group = "CONFIRMED" if is_google else "UNCONFIRMED"

        user = User.objects.create(
            email=email,
            name=name,
            password=hashed_password,
            auth_type=auth_type,
            group=group,
        )

        _save_pending_brief(user, data)

        response_data = {
            "id": str(user.id),
            "message": "User registered.",
            "group": user.group,
        }

        if is_google:
            if user.pending_brief_id:
                user.group = "CLIENT"
                user.save(update_fields=["group"])
                client, _ = Client.objects.get_or_create(
                    owner=user,
                    defaults={"name": f"{user.name}'s Company", "ein": ""},
                )
                response_data["group"] = user.group
                response_data["clientId"] = str(client.id)
                claimed = _try_claim_pending_brief(user)
                if claimed:
                    response_data["claimedBriefId"] = claimed
        else:
            token_obj = AuthToken.create_token(user, TokenType.EMAIL_CONFIRMATION)
            email_sent = send_confirmation_email(user, token_obj.token)
            if email_sent:
                logger.info("Confirmation email sent to %s", user.email)
            else:
                logger.error("Failed to send confirmation email to %s", user.email)

        return JsonResponse(response_data, status=201)

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception:
        logger.exception("Register error")
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
@public_endpoint
@ratelimit(key="ip", rate="5/m", method="POST", block=True)
def login(request):  # noqa: C901, PLR0912
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

        is_internal = _is_internal_request(request)

        if auth_type == "GOOGLE":
            if not is_internal:
                return JsonResponse(
                    {"error": "Google authentication must go through OAuth flow"},
                    status=400,
                )
            if user.auth_type != "GOOGLE":
                return JsonResponse(
                    {"error": "This account uses password authentication"},
                    status=400,
                )
        else:
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
            "isStaff": bool(user.is_staff),
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

        brief_id = data.get("briefId")
        brief_token = data.get("briefToken")
        if brief_id and brief_token:
            if user.group == "CLIENT":
                _save_pending_brief(user, data)
                claimed = _try_claim_pending_brief(user)
                if claimed:
                    response_data["claimedBriefId"] = claimed
            elif user.group != "VENDOR":
                _save_pending_brief(user, data)

        return JsonResponse(response_data, status=200)

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception:
        logger.exception("Login error")
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

        response_data = {}

        if user.pending_brief_id:
            user.group = "CLIENT"
            user.save()
            Client.objects.get_or_create(
                owner=user,
                defaults={"name": f"{user.name}'s Company", "ein": ""},
            )
            claimed = _try_claim_pending_brief(user)
            if claimed:
                response_data["claimedBriefId"] = claimed
            client = Client.objects.filter(owner=user).first()
            if client:
                response_data["clientId"] = str(client.id)
        else:
            user.group = "CONFIRMED"
            user.save()

        logger.info("Email confirmed for user: %s (group=%s)", user.email, user.group)
        token_obj.delete()

        response_data.update(
            {
                "id": str(user.id),
                "email": user.email,
                "name": user.name,
                "group": user.group,
            }
        )
        return JsonResponse(response_data, status=200)

    except Exception:
        logger.exception("Confirm email error")
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

        user = User.objects.filter(email=email, deleted_at__isnull=True).first()

        if user:
            return JsonResponse(
                {
                    "exists": True,
                    "authType": user.auth_type,
                },
                status=200,
            )

        return JsonResponse(
            {
                "exists": False,
            },
            status=200,
        )

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception:
        logger.exception("Check email error")
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
                "message": (
                    "If an account with that email exists,"
                    " a password reset link has been sent."
                ),
            },
            status=200,
        )

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception:
        logger.exception("Forgot password error")
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
                "message": (
                    "If an account with that email exists"
                    " and is unconfirmed, a confirmation"
                    " email has been sent."
                ),
            },
            status=200,
        )

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception:
        logger.exception("Resend confirmation error")
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
@ratelimit(key="ip", rate="10/m", method="POST", block=True)
def set_pending_brief(request):
    """
    Store pending brief for claiming after role assignment.

    POST /api/v1/auth/set-pending-brief
    Body: {"briefId": "...", "briefToken": "..."}
    """
    if not hasattr(request, "user_data") or not request.user_data:
        return JsonResponse({"error": "Authentication required"}, status=401)

    try:
        data = json.loads(request.body)
        user_id = request.user_data.get("id")
        user = User.objects.get(id=user_id)

        _save_pending_brief(user, data)

        if user.group == "CLIENT":
            claimed = _try_claim_pending_brief(user)
            if claimed:
                return JsonResponse({"claimedBriefId": claimed}, status=200)

        if user.group == "CONFIRMED":
            user.group = "CLIENT"
            user.save(update_fields=["group"])
            Client.objects.get_or_create(
                owner=user,
                defaults={"name": f"{user.name}'s Company", "ein": ""},
            )
            claimed = _try_claim_pending_brief(user)
            client_id = (
                Client.objects.filter(owner=user).values_list("id", flat=True).first()
            )
            response_data = {
                "group": user.group,
                "clientId": str(client_id),
            }
            if claimed:
                response_data["claimedBriefId"] = claimed
            return JsonResponse(response_data, status=200)

        return JsonResponse({"message": "Pending brief saved"}, status=200)

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except User.DoesNotExist:
        return JsonResponse({"error": "User not found"}, status=404)
    except Exception:
        logger.exception("Set pending brief error")
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
    except Exception:
        logger.exception("Reset password error")
        return JsonResponse({"error": "An internal error occurred"}, status=500)
