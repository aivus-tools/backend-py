from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include
from django.urls import path
from django.views import defaults as default_views
from django.views.generic import TemplateView

from aivus_backend.users.api import user_views

urlpatterns = [
    path("", TemplateView.as_view(template_name="pages/home.html"), name="home"),
    path(
        "about/",
        TemplateView.as_view(template_name="pages/about.html"),
        name="about",
    ),
    # Django Admin, use {% url 'admin:index' %}
    path(settings.ADMIN_URL, admin.site.urls),
    # User management
    path("users/", include("aivus_backend.users.urls", namespace="users")),
    path("accounts/", include("allauth.urls")),
    # API endpoints
    path("api/v1/auth/", include("aivus_backend.users.api.urls", namespace="auth_api")),
    path("api/v1/users/me", user_views.user_me, name="user-me"),
    path(
        "api/v1/users/profile/avatar",
        user_views.user_profile_avatar,
        name="user-profile-avatar",
    ),
    path("api/v1/users/profile", user_views.user_profile, name="user-profile"),
    path("api/v1/users/settings", user_views.user_settings, name="user-settings"),
    path(
        "api/v1/users/change-password",
        user_views.change_password,
        name="change-password",
    ),
    path(
        "api/v1/users/<uuid:user_id>/change-group",
        user_views.change_user_group,
        name="change-user-group",
    ),
    path("api/v1/users", user_views.get_users, name="get-users"),
    path(
        "api/v1/vendor/settings/logo",
        user_views.vendor_settings_logo,
        name="vendor-settings-logo",
    ),
    path(
        "api/v1/vendor/settings/slug/suggest",
        user_views.vendor_slug_suggest,
        name="vendor-slug-suggest",
    ),
    path("api/v1/vendor/settings", user_views.vendor_settings, name="vendor-settings"),
    # Catalog API
    path("api/v1/", include("aivus_backend.catalog.api.urls", namespace="catalog_api")),
    # Projects API (briefs, projects, offers)
    path(
        "api/v1/", include("aivus_backend.projects.api.urls", namespace="projects_api")
    ),
    # Vendors API (pre-vendors)
    path("api/v1/", include("aivus_backend.vendors.api.urls", namespace="vendors_api")),
    # TinyMCE
    path("tinymce/", include("tinymce.urls")),
    # Your stuff: custom urls includes go here
    # ...
    # Media files
    *static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT),
]


if settings.DEBUG:
    # This allows the error pages to be debugged during development, just visit
    # these url in browser to see how these error pages look like.
    urlpatterns += [
        path(
            "400/",
            default_views.bad_request,
            kwargs={"exception": Exception("Bad Request!")},
        ),
        path(
            "403/",
            default_views.permission_denied,
            kwargs={"exception": Exception("Permission Denied")},
        ),
        path(
            "404/",
            default_views.page_not_found,
            kwargs={"exception": Exception("Page not Found")},
        ),
        path("500/", default_views.server_error),
    ]
    if "debug_toolbar" in settings.INSTALLED_APPS:
        import debug_toolbar

        urlpatterns = [
            path("__debug__/", include(debug_toolbar.urls)),
            *urlpatterns,
        ]
