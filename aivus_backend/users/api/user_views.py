"""User API views."""

import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from aivus_backend.core.decorators import require_groups
from aivus_backend.users.models import Client
from aivus_backend.users.models import User
from aivus_backend.users.models import Vendor


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
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
@require_http_methods(["PATCH"])
@require_groups("VENDOR", "CLIENT", "SYSTEM", "CONFIRMED")
def change_user_group(request, user_id):
    """
    Change user group.

    PATCH /api/v1/users/:id/change-group
    Body: {"group": "VENDOR"}
    """
    try:
        body = request.body.decode() if isinstance(request.body, (bytes, bytearray)) else request.body
        data = json.loads(body or "{}")
        # Поддерживаем оба варианта: новый backend (group) и старый (newGroup)
        new_group = data.get("group") or data.get("newGroup")

        if not new_group:
            return JsonResponse({"error": "Group is required"}, status=400)

        user = User.objects.get(id=user_id)
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

        return JsonResponse(
            {
                "id": str(user.id),
                "email": user.email,
                "name": user.name,
                "group": user.group,
            },
            status=200,
        )

    except User.DoesNotExist:
        return JsonResponse({"error": "User not found"}, status=404)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
@require_http_methods(["GET"])
@require_groups("VENDOR", "CLIENT", "SYSTEM")
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

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
