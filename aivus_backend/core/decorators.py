"""Decorators for authentication and authorization."""

from functools import wraps

from django.http import JsonResponse


def public_endpoint(view_func):
    """
    Decorator to mark an endpoint as public (no authentication required).

    Usage:
        @public_endpoint
        def my_view(request):
            return JsonResponse({"message": "Hello World"})
    """
    view_func.is_public = True
    return view_func


def require_groups(*groups):
    """
    Decorator to require specific user groups for an endpoint.

    Usage:
        @require_groups("VENDOR", "CLIENT")
        def my_view(request):
            return JsonResponse({"message": "Hello"})
    """

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            # Check if user is authenticated
            if not hasattr(request, "user_data") or not request.user_data:
                return JsonResponse({"error": "Authentication required"}, status=401)

            user_group = request.user_data.get("group")
            if not user_group or user_group not in groups:
                return JsonResponse(
                    {"error": "Access denied"},
                    status=403,
                )

            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator
