"""Catalog API URLs."""

from django.urls import path

from .views import get_categories
from .views import get_entries
from .views import get_entry
from .views import get_units

app_name = "catalog"

urlpatterns = [
    path("categories", get_categories, name="categories"),
    path("entries", get_entries, name="entries"),
    path("entries/<uuid:entry_id>", get_entry, name="entry-detail"),
    path("units", get_units, name="units"),
]
