"""Email-agent API views (function-based): connect and manage mailboxes."""

from __future__ import annotations

import json

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from aivus_backend.core.decorators import require_groups
from aivus_backend.email_agent import mailbox
from aivus_backend.email_agent.api.serializers import serialize_account
from aivus_backend.email_agent.models import EmailAccount
from aivus_backend.email_agent.models import EmailAccountRole
from aivus_backend.email_agent.models import EmailAccountStatus
from aivus_backend.users.models import User
from aivus_backend.users.models import Vendor

VALID_ROLES = {role.value for role in EmailAccountRole}


def _vendor_for_request(request):
    user_id = request.user_data.get("id")
    user = User.objects.filter(id=user_id).first()
    if not user:
        return None
    return Vendor.objects.filter(owner=user, deleted_at__isnull=True).first()


@csrf_exempt
@require_http_methods(["GET"])
@require_groups("VENDOR", "SYSTEM")
def list_mailboxes(request):
    vendor = _vendor_for_request(request)
    if vendor is None:
        return JsonResponse({"error": "Vendor not found"}, status=404)
    accounts = EmailAccount.objects.filter(
        vendor=vendor, deleted_at__isnull=True
    ).order_by("role")
    return JsonResponse({"mailboxes": [serialize_account(a) for a in accounts]})


@csrf_exempt
@require_http_methods(["POST"])
@require_groups("VENDOR", "SYSTEM")
def connect_mailbox(request):
    vendor = _vendor_for_request(request)
    if vendor is None:
        return JsonResponse({"error": "Vendor not found"}, status=404)

    try:
        payload = json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        return JsonResponse({"error": "Invalid payload"}, status=400)

    role = payload.get("role")
    email = (payload.get("email") or "").strip()
    credential = payload.get("credential") or ""
    if role not in VALID_ROLES or not email or not credential:
        return JsonResponse(
            {"error": "role, email and credential are required"}, status=400
        )

    account = EmailAccount.objects.filter(
        vendor=vendor, role=role, deleted_at__isnull=True
    ).first()
    if account is None:
        account = EmailAccount(vendor=vendor, role=role)
    account.email = email
    account.credential = credential
    account.status = EmailAccountStatus.CONNECTED
    account.next_poll_at = timezone.now()

    try:
        mailbox.test_connection(account)
    except mailbox.MailboxError:
        return JsonResponse(
            {"error": "Could not connect to the mailbox with these credentials"},
            status=400,
        )

    account.save()
    return JsonResponse(serialize_account(account), status=201)


@csrf_exempt
@require_http_methods(["POST"])
@require_groups("VENDOR", "SYSTEM")
def disconnect_mailbox(request, account_id):
    vendor = _vendor_for_request(request)
    if vendor is None:
        return JsonResponse({"error": "Vendor not found"}, status=404)
    account = EmailAccount.objects.filter(
        id=account_id, vendor=vendor, deleted_at__isnull=True
    ).first()
    if account is None:
        return JsonResponse({"error": "Mailbox not found"}, status=404)

    account.credential = ""
    account.status = EmailAccountStatus.DISCONNECTED
    account.next_poll_at = None
    account.deleted_at = timezone.now()
    account.save(
        update_fields=[
            "credential",
            "status",
            "next_poll_at",
            "deleted_at",
            "updated_at",
        ]
    )
    return JsonResponse({"status": "disconnected"})
