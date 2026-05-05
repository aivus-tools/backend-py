"""Vendors API URL configuration."""

from django.urls import path

from .views import list_pre_vendors

app_name = "vendors_api"

urlpatterns = [
    path("pre-vendors", list_pre_vendors, name="pre-vendors"),
]
