"""Authentication middleware for HMAC and API Key."""

import hashlib
import hmac
import logging
import time

from django.conf import settings
from django.http import JsonResponse
from django.utils.deprecation import MiddlewareMixin

from aivus_backend.users.models import Client
from aivus_backend.users.models import User
from aivus_backend.users.models import Vendor

logger = logging.getLogger(__name__)

# Constants
# QA2-029: Reduced from 300s (5 min) to 60s (1 min) for tighter security
TIMESTAMP_TOLERANCE_SECONDS = 60


class HMACAuthenticationMiddleware(MiddlewareMixin):
    """
    HMAC Authentication Middleware.

    This middleware checks HMAC signature or API Key for authentication.
    Skips authentication for endpoints marked with @public_endpoint decorator.
    """

    WHITELISTED_PREFIXES = (
        "/admin/",
        "/accounts/",
        "/static/",
        "/media/",
        "/__debug__/",
        "/users/",
    )
    WHITELISTED_PATHS = {"/", "/about/"}

    def _should_skip_auth(self, path, view_func):
        """Check if authentication should be skipped."""
        if getattr(view_func, "is_public", False):
            return True
        if path.startswith(self.WHITELISTED_PREFIXES):
            return True
        return path in self.WHITELISTED_PATHS

    def _get_user_context(self, user):
        """Get vendor/client IDs for user. Always uses group from DB."""
        db_group = user.group
        vendor_id = None
        client_id = None

        if db_group == "VENDOR":
            vendor = Vendor.objects.filter(owner=user).first()
            vendor_id = str(vendor.id) if vendor else None

        if db_group == "CLIENT":
            client = Client.objects.filter(owner=user).first()
            client_id = str(client.id) if client else None

        return {
            "id": str(user.id),
            "email": user.email,
            "group": db_group,
            "vendor_id": vendor_id,
            "client_id": client_id,
        }

    def _validate_timestamp(self, timestamp_str):
        """Validate request timestamp."""
        try:
            timestamp = int(timestamp_str)
            current_time = int(time.time())
            if abs(current_time - timestamp) > TIMESTAMP_TOLERANCE_SECONDS:
                return None, "Request timestamp is too old"
            return timestamp, None
        except (ValueError, TypeError):
            return None, "Invalid timestamp format"

    def _verify_hmac(self, request, timestamp, user_id, user_group, signature):
        """Verify HMAC signature."""
        # Include query string in path for HMAC calculation
        path_with_query = request.path
        if request.META.get("QUERY_STRING"):
            path_with_query = f"{request.path}?{request.META['QUERY_STRING']}"

        message = (
            f"{request.method}:{path_with_query}:{timestamp}:{user_id}:{user_group}"
        )
        expected_signature = hmac.new(
            settings.HMAC_SECRET.encode(),
            message.encode(),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(signature, expected_signature)

    def process_view(self, request, view_func, view_args, view_kwargs):
        """Process view and authenticate user."""
        path = request.path

        # Skip authentication for whitelisted paths
        if self._should_skip_auth(path, view_func):
            return None

        # Get authentication headers
        api_key = request.headers.get("X-Api-Key")
        user_id = request.headers.get("X-User-Id")
        user_group = request.headers.get("X-User-Group")
        timestamp_str = request.headers.get("X-Timestamp")
        signature = request.headers.get("X-Signature")

        # Try API Key authentication first
        if (
            api_key
            and user_id
            and hmac.compare_digest(
                api_key.encode() if isinstance(api_key, str) else api_key,
                settings.API_KEY.encode()
                if isinstance(settings.API_KEY, str)
                else settings.API_KEY,
            )
        ):
            try:
                user = User.objects.get(id=user_id)
                request.user_data = self._get_user_context(user)
                return None
            except User.DoesNotExist:
                return JsonResponse({"error": "User not found"}, status=401)

        # Try HMAC authentication
        if not all([timestamp_str, signature, user_id, user_group]):
            return JsonResponse(
                {"error": "Missing authentication headers"},
                status=401,
            )

        # Validate timestamp
        timestamp, error = self._validate_timestamp(timestamp_str)
        if error:
            return JsonResponse({"error": error}, status=401)

        # Verify HMAC signature
        if not self._verify_hmac(
            request,
            timestamp,
            user_id,
            user_group,
            signature,
        ):
            return JsonResponse({"error": "Invalid HMAC signature"}, status=401)

        # Load user and attach to request
        try:
            user = User.objects.get(id=user_id)
            request.user_data = self._get_user_context(user)
        except User.DoesNotExist:
            return JsonResponse({"error": "User not found"}, status=401)

        return None
