"""Email-agent API views (function-based): connect and manage mailboxes."""

from __future__ import annotations

import json

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from aivus_backend.core.decorators import require_groups
from aivus_backend.email_agent import activity
from aivus_backend.email_agent import drafts as drafts_service
from aivus_backend.email_agent import feed
from aivus_backend.email_agent import mailbox
from aivus_backend.email_agent import onboarding
from aivus_backend.email_agent.api.serializers import serialize_account
from aivus_backend.email_agent.api.serializers import serialize_draft
from aivus_backend.email_agent.api.serializers import serialize_profile
from aivus_backend.email_agent.models import EmailAccount
from aivus_backend.email_agent.models import EmailAccountRole
from aivus_backend.email_agent.models import EmailAccountStatus
from aivus_backend.email_agent.models import EmailThread
from aivus_backend.email_agent.models import OutboundDraft
from aivus_backend.email_agent.models import OutboundDraftStatus
from aivus_backend.email_agent.models import VendorAgentProfile
from aivus_backend.users.models import User
from aivus_backend.users.models import Vendor

VALID_ROLES = {role.value for role in EmailAccountRole}


def _vendor_for_request(request):
    user_id = request.user_data.get("id")
    user = User.objects.filter(id=user_id).first()
    if not user:
        return None
    return Vendor.objects.filter(owner=user, deleted_at__isnull=True).first()


def _agent_context(vendor: Vendor) -> tuple[str, str]:
    """Resolve (producer_email, agent_email) for draft-view recipient pinning.

    Kept on the view boundary so the whole draft page uses the same identity;
    the sender pins the same pair at send time, so the preview cannot drift
    from what actually gets sent.
    """
    profile = VendorAgentProfile.objects.filter(vendor=vendor).first()
    producer_email = profile.producer_email if profile is not None else ""
    agent_account = EmailAccount.objects.filter(
        vendor=vendor,
        role=EmailAccountRole.AGENT,
        deleted_at__isnull=True,
    ).first()
    agent_email = agent_account.email if agent_account is not None else ""
    return producer_email, agent_email


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


def _draft_for_request(request, draft_id):
    vendor = _vendor_for_request(request)
    if vendor is None:
        return None, None
    draft = (
        OutboundDraft.objects.select_related(
            "thread", "thread__vendor", "in_reply_to_message"
        )
        .filter(id=draft_id, thread__vendor=vendor)
        .first()
    )
    return vendor, draft


@csrf_exempt
@require_http_methods(["GET"])
@require_groups("VENDOR", "SYSTEM")
def list_drafts(request):
    vendor = _vendor_for_request(request)
    if vendor is None:
        return JsonResponse({"error": "Vendor not found"}, status=404)
    # Only pending drafts are actionable; approve/edit/reject all reject a
    # non-pending draft with a 409. An expired-overdue first reply is surfaced
    # instead as its own follow-up dashboard bucket, so it never becomes a dead
    # card here with buttons that can only fail.
    queryset = (
        OutboundDraft.objects.filter(
            thread__vendor=vendor,
            status=OutboundDraftStatus.PENDING,
        )
        .select_related("thread", "in_reply_to_message")
        .order_by("-created_at")
    )
    producer_email, agent_email = _agent_context(vendor)
    return JsonResponse(
        {
            "drafts": [
                serialize_draft(
                    d, producer_email=producer_email, agent_email=agent_email
                )
                for d in queryset
            ]
        }
    )


@csrf_exempt
@require_http_methods(["POST"])
@require_groups("VENDOR", "SYSTEM")
def approve_draft(request, draft_id):
    vendor, draft = _draft_for_request(request, draft_id)
    if vendor is None:
        return JsonResponse({"error": "Vendor not found"}, status=404)
    if draft is None:
        return JsonResponse({"error": "Draft not found"}, status=404)

    # The optional edit-then-send body arrives as JSON; a bodyless approve is
    # valid, so a missing or non-JSON body is treated as "send as drafted".
    try:
        payload = json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        payload = {}
    edited_body = payload.get("body") if isinstance(payload, dict) else None

    try:
        sent = drafts_service.approve_draft(draft, edited_body=edited_body)
    except drafts_service.DraftError as error:
        return JsonResponse({"error": str(error)}, status=409)
    producer_email, agent_email = _agent_context(vendor)
    return JsonResponse(
        {
            "draft": serialize_draft(
                draft, producer_email=producer_email, agent_email=agent_email
            ),
            "messageId": sent.provider_message_id,
        }
    )


@csrf_exempt
@require_http_methods(["POST"])
@require_groups("VENDOR", "SYSTEM")
def edit_draft(request, draft_id):
    vendor, draft = _draft_for_request(request, draft_id)
    if vendor is None:
        return JsonResponse({"error": "Vendor not found"}, status=404)
    if draft is None:
        return JsonResponse({"error": "Draft not found"}, status=404)

    try:
        payload = json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        return JsonResponse({"error": "Invalid payload"}, status=400)
    body = (payload.get("body") or "").strip()
    if not body:
        return JsonResponse({"error": "body is required"}, status=400)

    try:
        drafts_service.edit_draft(draft, body)
    except drafts_service.DraftError as error:
        return JsonResponse({"error": str(error)}, status=409)
    producer_email, agent_email = _agent_context(vendor)
    return JsonResponse(
        {
            "draft": serialize_draft(
                draft, producer_email=producer_email, agent_email=agent_email
            )
        }
    )


@csrf_exempt
@require_http_methods(["POST"])
@require_groups("VENDOR", "SYSTEM")
def reject_draft(request, draft_id):
    vendor, draft = _draft_for_request(request, draft_id)
    if vendor is None:
        return JsonResponse({"error": "Vendor not found"}, status=404)
    if draft is None:
        return JsonResponse({"error": "Draft not found"}, status=404)

    try:
        drafts_service.reject_draft(draft)
    except drafts_service.DraftError as error:
        return JsonResponse({"error": str(error)}, status=409)
    producer_email, agent_email = _agent_context(vendor)
    return JsonResponse(
        {
            "draft": serialize_draft(
                draft, producer_email=producer_email, agent_email=agent_email
            )
        }
    )


@csrf_exempt
@require_http_methods(["GET", "PATCH"])
@require_groups("VENDOR", "SYSTEM")
def agent_profile(request):
    vendor = _vendor_for_request(request)
    if vendor is None:
        return JsonResponse({"error": "Vendor not found"}, status=404)
    profile, _created = VendorAgentProfile.objects.get_or_create(vendor=vendor)

    if request.method == "GET":
        return JsonResponse(serialize_profile(profile))

    try:
        data = json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        return JsonResponse({"error": "Invalid payload"}, status=400)
    try:
        onboarding.apply_profile_update(profile, data)
    except onboarding.ProfileValidationError as error:
        return JsonResponse({"error": str(error)}, status=400)
    profile.save()
    return JsonResponse(serialize_profile(profile))


@csrf_exempt
@require_http_methods(["GET"])
@require_groups("VENDOR", "SYSTEM")
def list_threads(request):
    vendor = _vendor_for_request(request)
    if vendor is None:
        return JsonResponse({"error": "Vendor not found"}, status=404)
    limit, offset = feed.clamp_page(request.GET.get("limit"), request.GET.get("offset"))
    return JsonResponse(feed.list_threads(vendor, limit=limit, offset=offset))


@csrf_exempt
@require_http_methods(["GET"])
@require_groups("VENDOR", "SYSTEM")
def list_followups(request):
    vendor = _vendor_for_request(request)
    if vendor is None:
        return JsonResponse({"error": "Vendor not found"}, status=404)
    return JsonResponse(feed.list_followups(vendor))


@csrf_exempt
@require_http_methods(["GET"])
@require_groups("VENDOR", "SYSTEM")
def thread_activity(request, thread_id):
    vendor = _vendor_for_request(request)
    if vendor is None:
        return JsonResponse({"error": "Vendor not found"}, status=404)
    thread = EmailThread.objects.filter(
        id=thread_id, vendor=vendor, deleted_at__isnull=True
    ).first()
    if thread is None:
        return JsonResponse({"error": "Thread not found"}, status=404)
    return JsonResponse(activity.serialize_activity(thread))


@csrf_exempt
@require_http_methods(["POST"])
@require_groups("VENDOR", "SYSTEM")
def prepare_followup(request, thread_id):
    vendor = _vendor_for_request(request)
    if vendor is None:
        return JsonResponse({"error": "Vendor not found"}, status=404)
    thread = EmailThread.objects.filter(
        id=thread_id, vendor=vendor, deleted_at__isnull=True
    ).first()
    if thread is None:
        return JsonResponse({"error": "Thread not found"}, status=404)
    try:
        draft = feed.prepare_followup(thread)
    except feed.FollowupError as error:
        return JsonResponse({"error": str(error)}, status=409)
    producer_email, agent_email = _agent_context(vendor)
    return JsonResponse(
        {
            "draft": serialize_draft(
                draft, producer_email=producer_email, agent_email=agent_email
            )
        },
        status=201,
    )
