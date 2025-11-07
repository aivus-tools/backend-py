"""API URLs for auth and user endpoints."""

from django.urls import path

from . import auth_views

app_name = "auth_api"

urlpatterns = [
    # Auth endpoints
    path("register", auth_views.register, name="register"),
    path("login", auth_views.login, name="login"),
    path("confirm-email", auth_views.confirm_email, name="confirm-email"),
    path("check-email", auth_views.check_email, name="check-email"),
    path("forgot-password", auth_views.forgot_password, name="forgot-password"),
    path("reset-password", auth_views.reset_password, name="reset-password"),
]
