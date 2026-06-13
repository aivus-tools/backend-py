"""User API views."""

import json
import logging
from decimal import Decimal

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from aivus_backend.core.decorators import require_groups
from aivus_backend.core.enums import UserGroup
from aivus_backend.core.slugs import validate_slug
from aivus_backend.users.models import Client
from aivus_backend.users.models import User
from aivus_backend.users.models import UserSettings
from aivus_backend.users.models import Vendor
from aivus_backend.users.models import VendorSettings as VendorSettingsModel
from aivus_backend.users.models import VendorWebhookKey
from aivus_backend.users.slug_suggest import suggest_slug

MAX_NAME_LENGTH = 255
MAX_LANGUAGE_LENGTH = 5

logger = logging.getLogger(__name__)


@csrf_exempt
@require_http_methods(["GET"])
def user_me(request):
    """
    Get current user information.

    GET /api/v1/users/me
    """
    if not hasattr(request, "user_data") or not request.user_data:
        return JsonResponse({"error": "User is not authenticated"}, status=401)

    user_data = request.user_data
    user_id = user_data.get("id")

    try:
        user = User.objects.get(id=user_id)

        response_data = {
            "id": str(user.id),
            "email": user.email,
            "name": user.name,
            "group": user.group,
            "position": user.position,
            "authType": user.auth_type,
        }

        # Add vendor_id or client_id if applicable
        if user.group == "VENDOR":
            vendor = Vendor.objects.filter(owner=user).first()
            if vendor:
                response_data["vendorId"] = str(vendor.id)

        if user.group == "CLIENT":
            client = Client.objects.filter(owner=user).first()
            if client:
                response_data["clientId"] = str(client.id)

        return JsonResponse(response_data, status=200)

    except User.DoesNotExist:
        return JsonResponse({"error": "User not found"}, status=404)
    except Exception:
        logger.exception("Error getting user info")
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@csrf_exempt
@require_http_methods(["PATCH"])
@require_groups("VENDOR", "CLIENT", "SYSTEM", "CONFIRMED")
def change_user_group(request, user_id):  # noqa: C901, PLR0912
    """
    Change user group.

    PATCH /api/v1/users/:id/change-group
    Body: {"group": "VENDOR"}
    """
    try:
        # Users can only change their own group
        if str(request.user_data["id"]) != str(user_id):
            return JsonResponse(
                {"error": "You can only change your own group"}, status=403
            )

        body = (
            request.body.decode()
            if isinstance(request.body, (bytes, bytearray))
            else request.body
        )
        data = json.loads(body or "{}")
        # Support both formats: new backend (group) and legacy (newGroup)
        new_group = data.get("group") or data.get("newGroup")

        if not new_group:
            return JsonResponse({"error": "Group is required"}, status=400)

        # Validate new_group is a valid UserGroup value
        valid_groups = [g.value for g in UserGroup]
        if new_group not in valid_groups:
            return JsonResponse(
                {"error": f"Invalid group. Must be one of: {', '.join(valid_groups)}"},
                status=400,
            )

        user = User.objects.get(id=user_id)

        # Only allow CONFIRMED -> VENDOR or CONFIRMED -> CLIENT transitions
        if user.group != UserGroup.CONFIRMED or new_group not in (
            UserGroup.VENDOR,
            UserGroup.CLIENT,
        ):
            return JsonResponse(
                {
                    "error": (
                        "Group change only allowed from CONFIRMED to VENDOR or CLIENT"
                    )
                },
                status=400,
            )

        user.group = new_group
        user.save()

        # Create Client or Vendor if needed
        if new_group == "VENDOR" and not Vendor.objects.filter(owner=user).exists():
            Vendor.objects.create(
                name=f"{user.name}'s Agency",
                owner=user,
            )

        if new_group == "CLIENT" and not Client.objects.filter(owner=user).exists():
            Client.objects.create(
                name=f"{user.name}'s Company",
                ein="",  # Will be filled later
                owner=user,
            )

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

        if user.group == "CLIENT":
            client = Client.objects.filter(owner=user).first()
            if client:
                response_data["clientId"] = str(client.id)

            if user.pending_brief_id:
                from aivus_backend.users.api.auth_views import (  # noqa: PLC0415
                    _try_claim_pending_brief,
                )

                claimed = _try_claim_pending_brief(user)
                if claimed:
                    response_data["claimedBriefId"] = claimed

        return JsonResponse(response_data, status=200)

    except User.DoesNotExist:
        return JsonResponse({"error": "User not found"}, status=404)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception:
        logger.exception("Error changing user group")
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@csrf_exempt
@require_http_methods(["GET"])
@require_groups("SYSTEM")
def get_users(request):
    """
    Get all users.

    GET /api/v1/users
    """
    try:
        users = User.objects.all()
        users_data = [
            {
                "id": str(user.id),
                "email": user.email,
                "name": user.name,
                "group": user.group,
                "position": user.position,
                "authType": user.auth_type,
            }
            for user in users
        ]

        return JsonResponse(users_data, safe=False, status=200)

    except Exception:
        logger.exception("Error getting users")
        return JsonResponse({"error": "An internal error occurred"}, status=500)


# ==================== Profile API ====================


@csrf_exempt
@require_http_methods(["GET", "PATCH"])
@require_groups("VENDOR", "CLIENT", "SYSTEM")
def user_profile(request):  # noqa: C901
    """Get or update current user profile.

    GET /api/v1/users/profile - Returns user info + vendor/client data
    PATCH /api/v1/users/profile - Update profile fields
    Body: {"name": "...", "company": "...", "position": "..."}
    """
    user_data = request.user_data
    user_id = user_data.get("id")

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return JsonResponse({"error": "User not found"}, status=404)

    if request.method == "GET":
        return JsonResponse(_build_profile_response(user))

    # PATCH - update profile
    try:
        data = json.loads(request.body)

        # Validate input lengths
        if "name" in data and len(data["name"]) > MAX_NAME_LENGTH:
            return JsonResponse(
                {"error": "Name must be 255 characters or fewer"}, status=400
            )

        # Update User fields
        if "name" in data:
            user.name = data["name"]
        if "position" in data:
            user.position = data["position"]
        user.save()

        # Update Vendor/Client company name if provided
        if "company" in data:
            if user.group == "VENDOR":
                vendor = Vendor.objects.filter(owner=user).first()
                if vendor:
                    vendor.name = data["company"]
                    vendor.save()
            elif user.group == "CLIENT":
                client = Client.objects.filter(owner=user).first()
                if client:
                    client.name = data["company"]
                    client.save()

        return JsonResponse(_build_profile_response(user))

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception:
        logger.exception("Error updating profile")
        return JsonResponse({"error": "An internal error occurred"}, status=500)


def _build_profile_response(user):
    """Build profile response dict for a user."""
    response_data = {
        "id": str(user.id),
        "email": user.email,
        "name": user.name,
        "group": user.group,
        "position": user.position,
        "authType": user.auth_type,
        "avatar_url": user.avatar.url if user.avatar else None,
        "createdAt": user.created_at.isoformat() if user.created_at else None,
        "isStaff": bool(user.is_staff),
    }

    if user.group == "VENDOR":
        vendor = Vendor.objects.filter(owner=user).first()
        if vendor:
            response_data["vendorId"] = str(vendor.id)
            response_data["company"] = vendor.name

    if user.group == "CLIENT":
        client = Client.objects.filter(owner=user).first()
        if client:
            response_data["clientId"] = str(client.id)
            response_data["company"] = client.name

    return response_data


# ==================== Settings API ====================


@csrf_exempt
@require_http_methods(["GET", "PATCH"])
@require_groups("VENDOR", "CLIENT", "SYSTEM")
def user_settings(request):
    """Get or update user settings.

    GET /api/v1/users/settings - Returns language, NDA, notification prefs
    PATCH /api/v1/users/settings - Update settings
    Body: {"language": "en", "nda_accepted": true, ...}
    """
    user_data = request.user_data
    user_id = user_data.get("id")

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return JsonResponse({"error": "User not found"}, status=404)

    # Get or create settings
    settings, _created = UserSettings.objects.get_or_create(user=user)

    if request.method == "GET":
        return JsonResponse(_build_settings_response(settings))

    # PATCH - update settings
    try:
        data = json.loads(request.body)

        if "language" in data:
            if len(data["language"]) > MAX_LANGUAGE_LENGTH:
                return JsonResponse(
                    {"error": "Language must be 5 characters or fewer"}, status=400
                )
            settings.language = data["language"]
        if "nda_accepted" in data:
            settings.nda_accepted = bool(data["nda_accepted"])
        if "notification_email" in data:
            settings.notification_email = bool(data["notification_email"])
        if "notification_browser" in data:
            settings.notification_browser = bool(data["notification_browser"])

        settings.save()
        return JsonResponse(_build_settings_response(settings))

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception:
        logger.exception("Error updating settings")
        return JsonResponse({"error": "An internal error occurred"}, status=500)


def _build_settings_response(settings):
    """Build settings response dict."""
    return {
        "id": str(settings.id),
        "language": settings.language,
        "nda_accepted": settings.nda_accepted,
        "notification_email": settings.notification_email,
        "notification_browser": settings.notification_browser,
        "updatedAt": settings.updated_at.isoformat() if settings.updated_at else None,
    }


# ==================== Change Password API ====================


@csrf_exempt
@require_http_methods(["POST"])
@require_groups("VENDOR", "CLIENT", "SYSTEM")
def change_password(request):
    """Change user password.

    POST /api/v1/users/change-password
    Body: {"current_password": "...", "new_password": "..."}
    """
    user_data = request.user_data
    user_id = user_data.get("id")

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return JsonResponse({"error": "User not found"}, status=404)

    try:
        data = json.loads(request.body)
        current_password = data.get("current_password")
        new_password = data.get("new_password")

        if not current_password or not new_password:
            return JsonResponse(
                {"error": "current_password and new_password are required"},
                status=400,
            )

        # QA3-012: Use Django's password validation instead of simple length check
        try:
            validate_password(new_password, user=user)
        except ValidationError as e:
            return JsonResponse(
                {"error": e.messages},
                status=400,
            )

        # Verify current password
        if not user.check_plain_password(current_password):
            return JsonResponse(
                {"error": "Current password is incorrect"},
                status=400,
            )

        # QA3-005: Use set_password() to update session auth hash,
        # invalidating all other sessions on next request
        user.set_password(new_password)
        user.save(update_fields=["password", "updated_at"])

        return JsonResponse({"message": "Password changed successfully"})

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception:
        logger.exception("Error changing password")
        return JsonResponse({"error": "An internal error occurred"}, status=500)


# ==================== Avatar Upload API ====================


@csrf_exempt
@require_http_methods(["POST"])
@require_groups("VENDOR", "CLIENT", "SYSTEM")
def user_profile_avatar(request):
    """Upload user avatar.

    POST /api/v1/users/profile/avatar
    Expects multipart/form-data with 'avatar' file field.
    """
    user_data = request.user_data
    user_id = user_data.get("id")

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return JsonResponse({"error": "User not found"}, status=404)

    avatar_file = request.FILES.get("avatar")
    if not avatar_file:
        return JsonResponse({"error": "No avatar file provided"}, status=400)

    # Delete old avatar if exists
    if user.avatar:
        user.avatar.delete(save=False)

    user.avatar = avatar_file
    user.save(update_fields=["avatar"])

    return JsonResponse({"avatar_url": user.avatar.url})


# ==================== Vendor Settings API ====================


@csrf_exempt
@require_http_methods(["GET", "PATCH"])
@require_groups("VENDOR", "SYSTEM")
def vendor_settings(request):
    user_data = request.user_data
    user_id = user_data.get("id")

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return JsonResponse({"error": "User not found"}, status=404)

    vendor = Vendor.objects.filter(owner=user).first()
    if not vendor:
        return JsonResponse({"error": "Vendor not found"}, status=404)

    settings, _created = VendorSettingsModel.objects.get_or_create(vendor=vendor)

    if request.method == "GET":
        return JsonResponse(_build_vendor_settings_response(settings))

    return _patch_vendor_settings(settings, request)


_VENDOR_PERCENT_FIELDS = {
    "fringesPercent": "fringes_percent",
    "handlingPercent": "handling_percent",
    "markupPercent": "markup_percent",
    "productionInsurancePercent": "production_insurance_percent",
    "productionFeePercent": "production_fee_percent",
    "postMarkupPercent": "post_markup_percent",
    "postInsurancePercent": "post_insurance_percent",
    "postTaxPercent": "post_tax_percent",
}


def _assign_vendor_settings_fields(settings, data):
    if "companyName" in data:
        settings.company_name = data["companyName"]
    if "agencyName" in data:
        settings.agency_name = data["agencyName"]
    if "leadNotificationEmail" in data:
        error = _apply_lead_notification_email(settings, data)
        if error:
            return error
    if "slug" in data:
        error = _apply_slug(settings, data)
        if error:
            return error
    for json_key, model_field in _VENDOR_PERCENT_FIELDS.items():
        if json_key in data:
            setattr(settings, model_field, Decimal(str(data[json_key])))
    return None


def _patch_vendor_settings(settings, request):
    try:
        data = json.loads(request.body)
        error = _assign_vendor_settings_fields(settings, data)
        if error:
            return error
        try:
            settings.save()
        except IntegrityError:
            return JsonResponse({"error": "This link is taken"}, status=409)
        return JsonResponse(_build_vendor_settings_response(settings))

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception:
        logger.exception("Error updating vendor settings")
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
@require_groups("VENDOR", "SYSTEM")
def vendor_settings_logo(request):
    user_data = request.user_data
    user_id = user_data.get("id")

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return JsonResponse({"error": "User not found"}, status=404)

    vendor = Vendor.objects.filter(owner=user).first()
    if not vendor:
        return JsonResponse({"error": "Vendor not found"}, status=404)

    settings, _created = VendorSettingsModel.objects.get_or_create(vendor=vendor)

    logo_file = request.FILES.get("logo")
    if not logo_file:
        return JsonResponse({"error": "No logo file provided"}, status=400)

    if settings.logo:
        settings.logo.delete(save=False)

    settings.logo = logo_file
    settings.save(update_fields=["logo"])

    return JsonResponse({"logoUrl": settings.logo.url})


@csrf_exempt
@require_http_methods(["GET"])
@require_groups("VENDOR", "SYSTEM")
def vendor_slug_suggest(request):
    user_data = request.user_data
    user_id = user_data.get("id")

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return JsonResponse({"error": "User not found"}, status=404)

    vendor = Vendor.objects.filter(owner=user).first()
    if not vendor:
        return JsonResponse({"error": "Vendor not found"}, status=404)

    settings, _created = VendorSettingsModel.objects.get_or_create(vendor=vendor)
    slug = suggest_slug(settings, use_llm=True)
    return JsonResponse({"slug": slug})


@csrf_exempt
@require_http_methods(["GET"])
@require_groups("VENDOR", "SYSTEM")
def vendor_slug_check(request):
    """Report whether the requested slug is free for this vendor to take.

    Unavailable when the format is invalid, the name is reserved, or another
    vendor already holds it. The vendor's own current slug counts as available
    so re-saving an unchanged value does not flip to taken.
    """
    vendor = _vendor_for_request(request)
    if vendor is None:
        return JsonResponse({"error": "Vendor not found"}, status=404)

    raw = (request.GET.get("slug") or "").strip()
    if validate_slug(raw) is not None:
        return JsonResponse({"available": False})

    taken = (
        VendorSettingsModel.objects.filter(slug=raw)
        .exclude(vendor_id=vendor.id)
        .exists()
    )
    return JsonResponse({"available": not taken})


@csrf_exempt
@require_http_methods(["GET"])
@require_groups("VENDOR", "SYSTEM")
def vendor_webhook_key(request):
    vendor = _vendor_for_request(request)
    if vendor is None:
        return JsonResponse({"error": "Vendor not found"}, status=404)
    key_row, _created = VendorWebhookKey.objects.get_or_create(vendor=vendor)
    return JsonResponse(_build_webhook_key_response(key_row))


@csrf_exempt
@require_http_methods(["POST"])
@require_groups("VENDOR", "SYSTEM")
def vendor_webhook_key_rotate(request):
    vendor = _vendor_for_request(request)
    if vendor is None:
        return JsonResponse({"error": "Vendor not found"}, status=404)
    key_row, _created = VendorWebhookKey.objects.get_or_create(vendor=vendor)
    if not _created:
        key_row.rotate()
    return JsonResponse(_build_webhook_key_response(key_row))


def _vendor_for_request(request):
    user_id = request.user_data.get("id")
    user = User.objects.filter(id=user_id).first()
    if not user:
        return None
    return Vendor.objects.filter(owner=user).first()


def _build_webhook_key_response(key_row):
    return {
        "key": key_row.key,
        "isActive": key_row.is_active,
        "createdAt": key_row.created_at.isoformat() if key_row.created_at else None,
        "rotatedAt": key_row.rotated_at.isoformat() if key_row.rotated_at else None,
    }


def _apply_lead_notification_email(settings, data):
    raw = data.get("leadNotificationEmail")
    email = (raw or "").strip()
    if email:
        try:
            validate_email(email)
        except ValidationError:
            return JsonResponse({"error": "Invalid email"}, status=400)
    settings.lead_notification_email = email
    return None


def _apply_slug(settings, data):
    raw = (data.get("slug") if isinstance(data.get("slug"), str) else "").strip()
    if not raw:
        settings.slug = None
        return None
    error = validate_slug(raw)
    if error:
        return JsonResponse({"error": error}, status=400)
    if (
        VendorSettingsModel.objects.filter(slug=raw)
        .exclude(vendor_id=settings.vendor_id)
        .exists()
    ):
        return JsonResponse({"error": "This link is taken"}, status=409)
    settings.slug = raw
    return None


def _build_vendor_settings_response(settings):
    return {
        "id": str(settings.id),
        "vendorId": str(settings.vendor_id),
        "logoUrl": settings.logo.url if settings.logo else None,
        "companyName": settings.company_name,
        "agencyName": settings.agency_name,
        "slug": settings.slug or None,
        "leadNotificationEmail": settings.lead_notification_email,
        "fringesPercent": str(settings.fringes_percent),
        "handlingPercent": str(settings.handling_percent),
        "markupPercent": str(settings.markup_percent),
        "productionInsurancePercent": str(settings.production_insurance_percent),
        "productionFeePercent": str(settings.production_fee_percent),
        "postMarkupPercent": str(settings.post_markup_percent),
        "postInsurancePercent": str(settings.post_insurance_percent),
        "postTaxPercent": str(settings.post_tax_percent),
        "updatedAt": settings.updated_at.isoformat() if settings.updated_at else None,
    }
